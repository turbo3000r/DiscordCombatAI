package coordinator

import (
	"testing"

	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/state"
)

func TestPrereleaseAdmissionIsManualOnly(t *testing.T) {
	t.Parallel()
	tests := []struct {
		reason  string
		allowed bool
	}{
		{reason: "auto_detected", allowed: false},
		{reason: "reconcile", allowed: false},
		{reason: "manual", allowed: true},
		{reason: "rollback", allowed: true},
	}
	for _, test := range tests {
		test := test
		t.Run(test.reason, func(t *testing.T) {
			t.Parallel()
			store := &coordinatorStore{}
			_, err := New(store).Submit(UpdateRequest{
				SchemaVersion: 1,
				RequestID:     "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
				TargetVersion: "v2.0.0-rc.1",
				Reason:        test.reason,
				RequestedAt:   "2026-07-15T17:05:00Z",
				SourceNodeID:  "node-a",
			}, "digest")
			if (err == nil) != test.allowed {
				t.Fatalf("reason %s error = %v", test.reason, err)
			}
		})
	}
}

type coordinatorStore struct{}

func (*coordinatorStore) Admit(_, _, target, _ string, _ bool) (state.Admission, error) {
	return state.Admission{
		Kind:   state.AdmissionStarted,
		Status: 202,
		Response: state.UpdateResponse{
			SchemaVersion: 1,
			TargetVersion: target,
		},
	}, nil
}

func (*coordinatorStore) Status() (state.Status, error) {
	return state.Status{}, nil
}

func (*coordinatorStore) VersionHistory() (string, string, error) {
	return "", "", nil
}
