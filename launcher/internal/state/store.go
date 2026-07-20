package state

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

var (
	ErrCorruptState     = errors.New("launcher state is corrupt")
	ErrRequestIntegrity = errors.New("request ID was previously used with different content")
	ErrReplay           = errors.New("authenticated request was replayed")
)

type Store struct {
	path             string
	lockPath         string
	requestRetention time.Duration
	replayRetention  time.Duration
	now              func() time.Time
	mu               sync.Mutex
}

func Open(path string, requestRetention, replayRetention time.Duration) (*Store, error) {
	return OpenWithLock(path, path+".lock", requestRetention, replayRetention)
}

func OpenWithLock(path, lockPath string, requestRetention, replayRetention time.Duration) (*Store, error) {
	store := &Store{
		path:             path,
		lockPath:         lockPath,
		requestRetention: requestRetention,
		replayRetention:  replayRetention,
		now:              func() time.Time { return time.Now().UTC() },
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(lockPath), 0o750); err != nil {
		return nil, err
	}
	err := store.write(func(file *File) error {
		if file.Active != nil && !file.Active.Terminal() && !processAlive(file.Active.OwnerPID) {
			now := store.now()
			file.Active.State = StateInterrupted
			file.Active.Error = "operation interrupted; submit a new update or rollback request"
			file.Active.UpdatedAt = now
			file.Active.CompletedAt = &now
			file.Last = cloneOperation(file.Active)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return store, nil
}

func (s *Store) Admit(requestID, bodyDigest, target, reason string, automaticRollback bool) (Admission, error) {
	var result Admission
	err := s.write(func(file *File) error {
		now := s.now()
		prune(file, now, s.requestRetention, s.replayRetention)
		if prior, ok := file.Requests[requestID]; ok {
			if prior.BodyDigest != bodyDigest {
				return ErrRequestIntegrity
			}
			result = Admission{Kind: AdmissionDuplicate, Status: prior.StatusCode, Response: prior.Response}
			return nil
		}
		if file.Active != nil && !file.Active.Terminal() {
			if file.Active.TargetVersion != target {
				result = Admission{Kind: AdmissionBusy, Status: 409, Operation: cloneOperation(file.Active)}
				return nil
			}
			response := acceptedResponse(requestID, file.Active.ID, target)
			file.Requests[requestID] = RequestRecord{
				RequestID: requestID, BodyDigest: bodyDigest, OperationID: file.Active.ID,
				StatusCode: 202, Response: response, CreatedAt: now,
			}
			result = Admission{Kind: AdmissionSameTarget, Status: 202, Response: response, Operation: cloneOperation(file.Active)}
			return nil
		}
		if target == file.CurrentVersion && target != "" {
			operationID := newUUID()
			if file.Last != nil && file.Last.TargetVersion == target {
				operationID = file.Last.ID
			}
			response := UpdateResponse{
				SchemaVersion: SchemaVersion, RequestID: requestID, OperationID: operationID,
				State: "already_current", TargetVersion: target,
			}
			file.Requests[requestID] = RequestRecord{
				RequestID: requestID, BodyDigest: bodyDigest, OperationID: operationID,
				StatusCode: 200, Response: response, CreatedAt: now, CompletedAt: &now,
			}
			result = Admission{Kind: AdmissionAlreadyCurrent, Status: 200, Response: response}
			return nil
		}
		operation := &Operation{
			ID: newUUID(), RequestID: requestID, TargetVersion: target, Reason: reason,
			State: StateAccepted, AutomaticRollback: automaticRollback, StartedAt: now,
			UpdatedAt: now, OwnerPID: os.Getpid(),
		}
		response := acceptedResponse(requestID, operation.ID, target)
		file.Active = operation
		file.Requests[requestID] = RequestRecord{
			RequestID: requestID, BodyDigest: bodyDigest, OperationID: operation.ID,
			StatusCode: 202, Response: response, CreatedAt: now,
		}
		result = Admission{Kind: AdmissionStarted, Status: 202, Response: response, Operation: cloneOperation(operation)}
		return nil
	})
	return result, err
}

func (s *Store) SetOperationState(operationID string, next OperationState, message string) error {
	return s.write(func(file *File) error {
		if file.Active == nil || file.Active.ID != operationID || file.Active.Terminal() {
			return fmt.Errorf("operation %s is not active", operationID)
		}
		file.Active.State = next
		file.Active.UpdatedAt = s.now()
		file.Active.Error = message
		if next == StateRollingBack {
			file.Active.RollbackAttempted = true
		}
		return nil
	})
}

func (s *Store) FinishSuccess(operationID string) error {
	return s.finish(operationID, StateSucceeded, "", true)
}

func (s *Store) FinishFailure(operationID, message string, halted bool) error {
	state := StateFailed
	if halted {
		state = StateHalted
	}
	return s.finish(operationID, state, message, false)
}

func (s *Store) FinishInterrupted(operationID, message string) error {
	return s.finish(operationID, StateInterrupted, message, false)
}

func (s *Store) finish(operationID string, terminal OperationState, message string, commitVersion bool) error {
	return s.write(func(file *File) error {
		if file.Active == nil || file.Active.ID != operationID || file.Active.Terminal() {
			return fmt.Errorf("operation %s is not active", operationID)
		}
		now := s.now()
		file.Active.State = terminal
		file.Active.UpdatedAt = now
		file.Active.CompletedAt = &now
		file.Active.Error = message
		if commitVersion && file.CurrentVersion != file.Active.TargetVersion {
			file.PreviousVersion = file.CurrentVersion
			file.CurrentVersion = file.Active.TargetVersion
		}
		for id, record := range file.Requests {
			if record.OperationID == operationID && terminal == StateSucceeded {
				record.StatusCode = 200
				record.Response.State = "already_current"
				record.CompletedAt = &now
				file.Requests[id] = record
			}
		}
		file.Last = cloneOperation(file.Active)
		return nil
	})
}

func (s *Store) Status() (Status, error) {
	var status Status
	err := s.read(func(file *File) error {
		status = Status{
			SchemaVersion: SchemaVersion, State: StateAccepted,
			CurrentVersion: file.CurrentVersion, LastError: "",
		}
		if file.PreviousVersion != "" {
			status.PreviousVersion = stringPointer(file.PreviousVersion)
		}
		operation := file.Active
		if operation == nil || operation.State == StateSucceeded {
			status.State = OperationState("IDLE")
			return nil
		}
		status.State = operation.State
		if operation.State == StateAccepted {
			status.State = StateAwaitDocker
		}
		status.OperationID = stringPointer(operation.ID)
		status.TargetVersion = stringPointer(operation.TargetVersion)
		status.LastError = operation.Error
		return nil
	})
	return status, err
}

func (s *Store) VersionHistory() (current, previous string, err error) {
	err = s.read(func(file *File) error {
		current, previous = file.CurrentVersion, file.PreviousVersion
		return nil
	})
	return
}

func (s *Store) CheckReplay(requestID, signature, digest, path string, allowIdentical bool) (bool, error) {
	duplicate := false
	err := s.write(func(file *File) error {
		now := s.now()
		prune(file, now, s.requestRetention, s.replayRetention)
		if prior, ok := file.Replays[requestID]; ok {
			if prior.Digest != digest || prior.Path != path {
				return ErrRequestIntegrity
			}
			if !allowIdentical {
				return ErrReplay
			}
			prior.Signature = signature
			prior.Accepted = now
			file.Replays[requestID] = prior
			duplicate = true
			return nil
		}
		file.Replays[requestID] = ReplayRecord{
			Signature: signature, Digest: digest, Path: path, Accepted: now,
		}
		return nil
	})
	return duplicate, err
}

func (s *Store) read(fn func(*File) error) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	lock, err := acquireFileLock(s.lockPath)
	if err != nil {
		return err
	}
	defer lock.Close()
	file, err := s.load()
	if err != nil {
		return err
	}
	return fn(file)
}

func (s *Store) write(fn func(*File) error) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	lock, err := acquireFileLock(s.lockPath)
	if err != nil {
		return err
	}
	defer lock.Close()
	file, err := s.load()
	if err != nil {
		return err
	}
	if err := fn(file); err != nil {
		return err
	}
	return writeAtomic(s.path, file)
}

func (s *Store) load() (*File, error) {
	raw, err := os.ReadFile(s.path)
	if errors.Is(err, os.ErrNotExist) {
		return &File{SchemaVersion: SchemaVersion, Requests: map[string]RequestRecord{}, Replays: map[string]ReplayRecord{}}, nil
	}
	if err != nil {
		return nil, err
	}
	if len(raw) == 0 {
		return nil, fmt.Errorf("%w: empty file", ErrCorruptState)
	}
	var file File
	if err := json.Unmarshal(raw, &file); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrCorruptState, err)
	}
	if file.SchemaVersion != SchemaVersion {
		return nil, fmt.Errorf("%w: unsupported schema_version %d", ErrCorruptState, file.SchemaVersion)
	}
	if file.Requests == nil {
		file.Requests = map[string]RequestRecord{}
	}
	if file.Replays == nil {
		file.Replays = map[string]ReplayRecord{}
	}
	return &file, nil
}

func writeAtomic(path string, value *File) error {
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(append(raw, '\n')); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return replaceFile(tmpName, path)
}

func acceptedResponse(requestID, operationID, target string) UpdateResponse {
	return UpdateResponse{
		SchemaVersion: SchemaVersion, RequestID: requestID, OperationID: operationID,
		State: "accepted", TargetVersion: target,
	}
}

func prune(file *File, now time.Time, requestRetention, replayRetention time.Duration) {
	for id, record := range file.Requests {
		if now.Sub(record.CreatedAt) > requestRetention {
			delete(file.Requests, id)
		}
	}
	for id, record := range file.Replays {
		if now.Sub(record.Accepted) > replayRetention {
			delete(file.Replays, id)
		}
	}
}

func newUUID() string {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic("cryptographic random source unavailable: " + err.Error())
	}
	value[6] = (value[6] & 0x0f) | 0x40
	value[8] = (value[8] & 0x3f) | 0x80
	raw := hex.EncodeToString(value[:])
	return raw[0:8] + "-" + raw[8:12] + "-" + raw[12:16] + "-" + raw[16:20] + "-" + raw[20:32]
}

func Digest(body []byte) string {
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}

func cloneOperation(operation *Operation) *Operation {
	if operation == nil {
		return nil
	}
	copy := *operation
	return &copy
}

func stringPointer(value string) *string {
	return &value
}
