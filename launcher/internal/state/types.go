package state

import "time"

const SchemaVersion = 1

type OperationState string

const (
	StateAccepted    OperationState = "ACCEPTED"
	StateAwaitDocker OperationState = "AWAIT_DOCKER"
	StatePulling     OperationState = "PULLING"
	StateRecreating  OperationState = "RECREATING"
	StateVerifying   OperationState = "VERIFYING"
	StateRollingBack OperationState = "ROLLING_BACK"
	StateSucceeded   OperationState = "SUCCEEDED"
	StateFailed      OperationState = "FAILED"
	StateHalted      OperationState = "HALTED"
	StateInterrupted OperationState = "INTERRUPTED"
)

type UpdateResponse struct {
	SchemaVersion int    `json:"schema_version"`
	RequestID     string `json:"request_id"`
	OperationID   string `json:"operation_id"`
	State         string `json:"state"`
	TargetVersion string `json:"target_version"`
}

type RequestRecord struct {
	RequestID   string         `json:"request_id"`
	BodyDigest  string         `json:"body_digest"`
	OperationID string         `json:"operation_id"`
	StatusCode  int            `json:"status_code"`
	Response    UpdateResponse `json:"response"`
	CreatedAt   time.Time      `json:"created_at"`
	CompletedAt *time.Time     `json:"completed_at,omitempty"`
}

type Operation struct {
	ID                string         `json:"id"`
	RequestID         string         `json:"request_id"`
	TargetVersion     string         `json:"target_version"`
	Reason            string         `json:"reason"`
	State             OperationState `json:"state"`
	AutomaticRollback bool           `json:"automatic_rollback"`
	RollbackAttempted bool           `json:"rollback_attempted"`
	StartedAt         time.Time      `json:"started_at"`
	UpdatedAt         time.Time      `json:"updated_at"`
	OwnerPID          int            `json:"owner_pid"`
	CompletedAt       *time.Time     `json:"completed_at,omitempty"`
	Error             string         `json:"error,omitempty"`
}

func (o *Operation) Terminal() bool {
	if o == nil {
		return true
	}
	switch o.State {
	case StateSucceeded, StateFailed, StateHalted, StateInterrupted:
		return true
	default:
		return false
	}
}

type ReplayRecord struct {
	Signature string    `json:"signature"`
	Digest    string    `json:"digest"`
	Path      string    `json:"path"`
	Accepted  time.Time `json:"accepted_at"`
}

type File struct {
	SchemaVersion   int                      `json:"schema_version"`
	CurrentVersion  string                   `json:"current_version"`
	PreviousVersion string                   `json:"previous_version,omitempty"`
	Active          *Operation               `json:"active_operation,omitempty"`
	Last            *Operation               `json:"last_operation,omitempty"`
	Requests        map[string]RequestRecord `json:"requests"`
	Replays         map[string]ReplayRecord  `json:"replays"`
}

type Status struct {
	SchemaVersion   int            `json:"schema_version"`
	State           OperationState `json:"state"`
	CurrentVersion  string         `json:"current_version"`
	PreviousVersion *string        `json:"previous_version"`
	OperationID     *string        `json:"operation_id"`
	TargetVersion   *string        `json:"target_version"`
	LastError       string         `json:"last_error,omitempty"`
}

type AdmissionKind int

const (
	AdmissionStarted AdmissionKind = iota
	AdmissionDuplicate
	AdmissionSameTarget
	AdmissionAlreadyCurrent
	AdmissionBusy
)

type Admission struct {
	Kind      AdmissionKind
	Status    int
	Response  UpdateResponse
	Operation *Operation
}
