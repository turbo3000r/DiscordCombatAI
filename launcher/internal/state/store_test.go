package state

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestAdmissionDedupBusyAndRestart(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "state.json")
	store, err := Open(path, 24*time.Hour, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	first, err := store.Admit("00000000-0000-4000-8000-000000000001", "digest-1", "v1.2.3", "manual", true)
	if err != nil || first.Kind != AdmissionStarted {
		t.Fatalf("first admission = %#v, %v", first, err)
	}
	duplicate, err := store.Admit("00000000-0000-4000-8000-000000000001", "digest-1", "v1.2.3", "manual", true)
	if err != nil || duplicate.Kind != AdmissionDuplicate || duplicate.Response.OperationID != first.Response.OperationID {
		t.Fatalf("duplicate admission = %#v, %v", duplicate, err)
	}
	if _, err := store.Admit("00000000-0000-4000-8000-000000000001", "changed", "v1.2.3", "manual", true); !errors.Is(err, ErrRequestIntegrity) {
		t.Fatalf("integrity error = %v", err)
	}
	same, err := store.Admit("00000000-0000-4000-8000-000000000002", "digest-2", "v1.2.3", "manual", true)
	if err != nil || same.Kind != AdmissionSameTarget || same.Response.OperationID != first.Response.OperationID {
		t.Fatalf("same-target admission = %#v, %v", same, err)
	}
	busy, err := store.Admit("00000000-0000-4000-8000-000000000003", "digest-3", "v2.0.0", "manual", true)
	if err != nil || busy.Kind != AdmissionBusy {
		t.Fatalf("busy admission = %#v, %v", busy, err)
	}
	restarted, err := Open(path, 24*time.Hour, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	again, err := restarted.Admit("00000000-0000-4000-8000-000000000001", "digest-1", "v1.2.3", "manual", true)
	if err != nil || again.Kind != AdmissionDuplicate {
		t.Fatalf("restart duplicate = %#v, %v", again, err)
	}
}

func TestConcurrentAdmissionHasOneOperation(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "state.json")
	store, err := Open(path, 24*time.Hour, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	secondStore, err := Open(path, 24*time.Hour, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	stores := []*Store{store, secondStore}
	const count = 32
	results := make(chan Admission, count)
	errorsChannel := make(chan error, count)
	var wait sync.WaitGroup
	for i := 0; i < count; i++ {
		wait.Add(1)
		go func(index int) {
			defer wait.Done()
			id := newUUID()
			admission, err := stores[index%len(stores)].Admit(id, id, "v1.2.3", "manual", true)
			results <- admission
			errorsChannel <- err
		}(i)
	}
	wait.Wait()
	close(results)
	close(errorsChannel)
	for err := range errorsChannel {
		if err != nil {
			t.Fatal(err)
		}
	}
	started := 0
	operationID := ""
	for result := range results {
		if result.Kind == AdmissionStarted {
			started++
		}
		if operationID == "" {
			operationID = result.Response.OperationID
		} else if result.Response.OperationID != operationID {
			t.Fatalf("multiple operation IDs: %s and %s", operationID, result.Response.OperationID)
		}
	}
	if started != 1 {
		t.Fatalf("started operations = %d", started)
	}
}

func TestInterruptedOperationIsVisibleAndReadmitted(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "state.json")
	now := time.Now().UTC()
	raw, err := json.Marshal(File{
		SchemaVersion: SchemaVersion,
		Active: &Operation{
			ID: "00000000-0000-4000-8000-000000000010", RequestID: "00000000-0000-4000-8000-000000000011",
			TargetVersion: "v1.2.3", Reason: "manual", State: StatePulling,
			OwnerPID: -1, StartedAt: now, UpdatedAt: now,
		},
		Requests: map[string]RequestRecord{}, Replays: map[string]ReplayRecord{},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := Open(path, 24*time.Hour, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	status, err := store.Status()
	if err != nil {
		t.Fatal(err)
	}
	if status.State != StateInterrupted || status.OperationID == nil {
		t.Fatalf("status = %#v", status)
	}
	admission, err := store.Admit(newUUID(), "new", "v1.2.4", "manual", true)
	if err != nil || admission.Kind != AdmissionStarted {
		t.Fatalf("new admission = %#v, %v", admission, err)
	}
}

func TestCorruptAndTruncatedStateRejected(t *testing.T) {
	t.Parallel()
	for _, body := range []string{"", `{"schema_version":1,"active_operation":`} {
		path := filepath.Join(t.TempDir(), "state.json")
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := Open(path, 24*time.Hour, 10*time.Minute); !errors.Is(err, ErrCorruptState) {
			t.Fatalf("Open(%q) error = %v", body, err)
		}
	}
}

func TestOpenWithLockUsesConfiguredLockPath(t *testing.T) {
	t.Parallel()
	directory := t.TempDir()
	statePath := filepath.Join(directory, "state", "launcher.json")
	lockPath := filepath.Join(directory, "locks", "launcher.lock")
	if _, err := OpenWithLock(statePath, lockPath, 24*time.Hour, 10*time.Minute); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(lockPath); err != nil {
		t.Fatalf("configured lock was not created: %v", err)
	}
	if _, err := os.Stat(statePath + ".lock"); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("default lock path was unexpectedly used: %v", err)
	}
}
