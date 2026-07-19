package engine

import (
	"context"
	"errors"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/state"
)

func TestRunnerSuccessPullsCompleteSetBeforeRecreate(t *testing.T) {
	t.Parallel()
	events := &eventRecorder{}
	docker := &fakeDocker{events: events}
	compose := &fakeCompose{events: events}
	store := &fakeStore{current: "v1.0.0", previous: "v0.9.0"}
	runner := NewRunner(docker, compose, fakeVerifier{events: events}, store, discardLogger{}, "ghcr.io/acme/dca", time.Millisecond, time.Millisecond, time.Second)
	operation := testOperation("v1.1.0", true)

	runner.Run(context.Background(), operation)

	expected := []string{
		"ping",
		"pull:ghcr.io/acme/dca/head:v1.1.0",
		"pull:ghcr.io/acme/dca/bot:v1.1.0",
		"pull:ghcr.io/acme/dca/ai_worker:v1.1.0",
		"compose:v1.1.0",
		"verify:v1.1.0",
	}
	if actual := events.snapshot(); !reflect.DeepEqual(actual, expected) {
		t.Fatalf("events = %#v", actual)
	}
	if !store.succeeded || store.failed {
		t.Fatalf("store result: %#v", store)
	}
}

func TestRunnerPartialPullNeverRecreates(t *testing.T) {
	t.Parallel()
	events := &eventRecorder{}
	docker := &fakeDocker{events: events, pullErrorAt: 2}
	compose := &fakeCompose{events: events}
	store := &fakeStore{current: "v1.0.0"}
	runner := NewRunner(docker, compose, fakeVerifier{events: events}, store, discardLogger{}, "ghcr.io/acme/dca", time.Millisecond, time.Millisecond, time.Second)

	runner.Run(context.Background(), testOperation("v1.1.0", true))

	for _, event := range events.snapshot() {
		if len(event) >= 8 && event[:8] == "compose:" {
			t.Fatalf("compose ran after partial pull: %#v", events.snapshot())
		}
	}
	if !store.failed || store.halted {
		t.Fatalf("store result: %#v", store)
	}
}

func TestRunnerVerificationFailureRollsBackExactlyOnce(t *testing.T) {
	t.Parallel()
	events := &eventRecorder{}
	docker := &fakeDocker{events: events}
	compose := &fakeCompose{events: events}
	store := &fakeStore{current: "v1.0.0", previous: "v0.9.0"}
	verifier := &sequenceVerifier{events: events, errors: []error{errors.New("unhealthy"), nil}}
	runner := NewRunner(docker, compose, verifier, store, discardLogger{}, "ghcr.io/acme/dca", time.Millisecond, time.Millisecond, time.Second)

	runner.Run(context.Background(), testOperation("v1.1.0", true))

	if compose.calls != 2 {
		t.Fatalf("compose calls = %d", compose.calls)
	}
	if verifier.calls != 2 {
		t.Fatalf("verify calls = %d", verifier.calls)
	}
	if !store.failed || store.halted || !store.rollbackMarked {
		t.Fatalf("store result: %#v", store)
	}
}

func TestRunnerRollbackFailureHaltsWithoutFlapping(t *testing.T) {
	t.Parallel()
	events := &eventRecorder{}
	docker := &fakeDocker{events: events}
	compose := &fakeCompose{events: events}
	store := &fakeStore{current: "v1.0.0"}
	verifier := &sequenceVerifier{events: events, errors: []error{errors.New("target failed"), errors.New("rollback failed")}}
	runner := NewRunner(docker, compose, verifier, store, discardLogger{}, "ghcr.io/acme/dca", time.Millisecond, time.Millisecond, time.Second)

	runner.Run(context.Background(), testOperation("v1.1.0", true))

	if verifier.calls != 2 || compose.calls != 2 || !store.halted {
		t.Fatalf("calls verify=%d compose=%d store=%#v", verifier.calls, compose.calls, store)
	}
}

func testOperation(target string, automaticRollback bool) *state.Operation {
	return &state.Operation{
		ID: "00000000-0000-4000-8000-000000000001", TargetVersion: target,
		State: state.StateAccepted, AutomaticRollback: automaticRollback,
	}
}

type eventRecorder struct {
	mu     sync.Mutex
	events []string
}

func (r *eventRecorder) add(event string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.events = append(r.events, event)
}

func (r *eventRecorder) snapshot() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.events...)
}

type fakeDocker struct {
	events      *eventRecorder
	pulls       int
	pullErrorAt int
}

func (d *fakeDocker) Ping(context.Context) error {
	d.events.add("ping")
	return nil
}

func (d *fakeDocker) Pull(_ context.Context, image string) error {
	d.pulls++
	d.events.add("pull:" + image)
	if d.pullErrorAt == d.pulls {
		return errors.New("pull failed")
	}
	return nil
}

func (d *fakeDocker) Close() error { return nil }

type fakeCompose struct {
	events *eventRecorder
	calls  int
}

func (c *fakeCompose) Recreate(_ context.Context, images map[string]string) error {
	c.calls++
	version := images["head"][len(images["head"])-len("v1.1.0"):]
	if images["head"] != images["bot"][:len(images["bot"])-len("bot:v1.1.0")]+"head:"+version {
		return errors.New("mixed image set")
	}
	c.events.add("compose:" + version)
	return nil
}

type fakeVerifier struct {
	events *eventRecorder
}

func (v fakeVerifier) Verify(_ context.Context, target string) error {
	v.events.add("verify:" + target)
	return nil
}

type sequenceVerifier struct {
	events *eventRecorder
	errors []error
	calls  int
}

func (v *sequenceVerifier) Verify(_ context.Context, target string) error {
	v.events.add("verify:" + target)
	index := v.calls
	v.calls++
	if index >= len(v.errors) {
		return errors.New("unexpected verification")
	}
	return v.errors[index]
}

type fakeStore struct {
	current        string
	previous       string
	succeeded      bool
	failed         bool
	halted         bool
	rollbackMarked bool
}

func (s *fakeStore) SetOperationState(_ string, next state.OperationState, _ string) error {
	if next == state.StateRollingBack {
		s.rollbackMarked = true
	}
	return nil
}

func (s *fakeStore) FinishSuccess(string) error {
	s.succeeded = true
	return nil
}

func (s *fakeStore) FinishFailure(_ string, _ string, halted bool) error {
	s.failed = true
	s.halted = halted
	return nil
}

func (s *fakeStore) FinishInterrupted(string, string) error {
	s.failed = true
	return nil
}

func (s *fakeStore) VersionHistory() (string, string, error) {
	return s.current, s.previous, nil
}

type discardLogger struct{}

func (discardLogger) Info(string, string, ...any)    {}
func (discardLogger) Warning(string, string, ...any) {}
func (discardLogger) Error(string, string, ...any)   {}
