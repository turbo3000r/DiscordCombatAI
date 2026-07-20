package coordinator

import (
	"errors"
	"strings"
	"time"

	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/auth"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/state"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/version"
)

var (
	ErrMalformed     = errors.New("malformed update request")
	ErrUnprocessable = errors.New("unsupported update request")
)

type UpdateRequest struct {
	SchemaVersion int    `json:"schema_version"`
	RequestID     string `json:"request_id"`
	TargetVersion string `json:"target_version"`
	Reason        string `json:"reason"`
	RequestedAt   string `json:"requested_at"`
	SourceNodeID  string `json:"source_node_id"`
}

type Store interface {
	Admit(string, string, string, string, bool) (state.Admission, error)
	Status() (state.Status, error)
	VersionHistory() (string, string, error)
}

type Coordinator struct {
	store Store
}

func New(store Store) *Coordinator {
	return &Coordinator{store: store}
}

func (c *Coordinator) Submit(request UpdateRequest, bodyDigest string) (state.Admission, error) {
	if request.SchemaVersion != 1 {
		return state.Admission{}, ErrMalformed
	}
	normalizedID, err := auth.NormalizeUUID(request.RequestID)
	if err != nil || normalizedID != request.RequestID {
		return state.Admission{}, ErrMalformed
	}
	if request.SourceNodeID == "" || len(request.SourceNodeID) > 128 || strings.TrimSpace(request.SourceNodeID) != request.SourceNodeID {
		return state.Admission{}, ErrMalformed
	}
	requestedAt, err := time.Parse(time.RFC3339, request.RequestedAt)
	if err != nil || requestedAt.IsZero() {
		return state.Admission{}, ErrMalformed
	}
	switch request.Reason {
	case "auto_detected", "manual", "rollback", "reconcile":
	default:
		return state.Admission{}, ErrUnprocessable
	}
	allowPrerelease := request.Reason == "manual" || request.Reason == "rollback"
	if err := version.Validate(request.TargetVersion, allowPrerelease); err != nil {
		return state.Admission{}, ErrUnprocessable
	}
	return c.store.Admit(
		request.RequestID, bodyDigest, request.TargetVersion, request.Reason,
		request.Reason != "rollback",
	)
}

func (c *Coordinator) Status() (state.Status, error) {
	return c.store.Status()
}

func (c *Coordinator) RollbackRequest(requestID string, now time.Time) (UpdateRequest, error) {
	_, previous, err := c.store.VersionHistory()
	if err != nil {
		return UpdateRequest{}, err
	}
	if previous == "" {
		return UpdateRequest{}, errors.New("no previous known-good version is available")
	}
	return UpdateRequest{
		SchemaVersion: 1,
		RequestID:     requestID,
		TargetVersion: previous,
		Reason:        "rollback",
		RequestedAt:   now.UTC().Format(time.RFC3339),
		SourceNodeID:  "launcher-cli",
	}, nil
}
