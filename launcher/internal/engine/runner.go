package engine

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/state"
)

type StateStore interface {
	SetOperationState(string, state.OperationState, string) error
	FinishSuccess(string) error
	FinishFailure(string, string, bool) error
	FinishInterrupted(string, string) error
	VersionHistory() (string, string, error)
}

type EventLogger interface {
	Info(string, string, ...any)
	Warning(string, string, ...any)
	Error(string, string, ...any)
}

type Runner struct {
	docker         Docker
	compose        Compose
	verifier       Verifier
	store          StateStore
	logger         EventLogger
	namespace      string
	dockerRetry    time.Duration
	dockerRetryMax time.Duration
	healthTimeout  time.Duration
}

func NewRunner(
	docker Docker,
	compose Compose,
	verifier Verifier,
	store StateStore,
	logger EventLogger,
	namespace string,
	dockerRetry, dockerRetryMax, healthTimeout time.Duration,
) *Runner {
	return &Runner{
		docker: docker, compose: compose, verifier: verifier, store: store, logger: logger,
		namespace: namespace, dockerRetry: dockerRetry, dockerRetryMax: dockerRetryMax,
		healthTimeout: healthTimeout,
	}
}

func (r *Runner) Run(ctx context.Context, operation *state.Operation) {
	if operation == nil {
		return
	}
	r.logger.Info("runner", "operation %s admitted for %s", operation.ID, operation.TargetVersion)
	if err := r.waitDocker(ctx, operation.ID); err != nil {
		r.finishBeforeRecreate(operation.ID, err)
		return
	}
	if err := r.store.SetOperationState(operation.ID, state.StatePulling, ""); err != nil {
		r.finishBeforeRecreate(operation.ID, err)
		return
	}
	images := Images(r.namespace, operation.TargetVersion)
	if err := r.pullAll(ctx, images); err != nil {
		r.finishBeforeRecreate(operation.ID, err)
		return
	}
	if err := r.store.SetOperationState(operation.ID, state.StateRecreating, ""); err != nil {
		r.finishBeforeRecreate(operation.ID, err)
		return
	}
	if err := r.compose.Recreate(ctx, images); err != nil {
		r.afterRecreateFailure(ctx, operation, err)
		return
	}
	if err := r.store.SetOperationState(operation.ID, state.StateVerifying, ""); err != nil {
		r.afterRecreateFailure(ctx, operation, err)
		return
	}
	verifyContext, cancel := context.WithTimeout(ctx, r.healthTimeout)
	err := r.verifier.Verify(verifyContext, operation.TargetVersion)
	cancel()
	if err != nil {
		r.afterRecreateFailure(ctx, operation, err)
		return
	}
	if err := r.store.FinishSuccess(operation.ID); err != nil {
		r.logger.Error("runner", "could not persist successful operation %s: %v", operation.ID, err)
		return
	}
	r.logger.Info("runner", "operation %s verified %s", operation.ID, operation.TargetVersion)
}

func (r *Runner) waitDocker(ctx context.Context, operationID string) error {
	if err := r.store.SetOperationState(operationID, state.StateAwaitDocker, ""); err != nil {
		return err
	}
	started := time.Now()
	for {
		pingContext, cancel := context.WithTimeout(ctx, 5*time.Second)
		err := r.docker.Ping(pingContext)
		cancel()
		if err == nil {
			return nil
		}
		if r.dockerRetryMax > 0 && time.Since(started) >= r.dockerRetryMax {
			return fmt.Errorf("Docker daemon unavailable: %w", err)
		}
		r.logger.Warning("docker", "Docker daemon unavailable; retrying")
		timer := time.NewTimer(r.dockerRetry)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}

func (r *Runner) pullAll(ctx context.Context, images map[string]string) error {
	for _, service := range ApplicationServices {
		if err := r.docker.Pull(ctx, images[service]); err != nil {
			return err
		}
		r.logger.Info("docker", "pulled %s", images[service])
	}
	return nil
}

func (r *Runner) afterRecreateFailure(ctx context.Context, operation *state.Operation, updateError error) {
	if ctx.Err() != nil {
		_ = r.store.FinishInterrupted(operation.ID, "operation interrupted; submit a new request")
		r.logger.Warning("runner", "operation %s interrupted", operation.ID)
		return
	}
	if !operation.AutomaticRollback {
		_ = r.store.FinishFailure(operation.ID, updateError.Error(), false)
		r.logger.Error("runner", "operation %s failed: %v", operation.ID, updateError)
		return
	}
	current, _, err := r.store.VersionHistory()
	if err != nil || current == "" {
		if err == nil {
			err = errors.New("no previous known-good version")
		}
		message := fmt.Sprintf("%v; automatic rollback unavailable: %v", updateError, err)
		_ = r.store.FinishFailure(operation.ID, message, true)
		r.logger.Error("runner", "operation %s halted: %s", operation.ID, message)
		return
	}
	if err := r.store.SetOperationState(operation.ID, state.StateRollingBack, updateError.Error()); err != nil {
		_ = r.store.FinishFailure(operation.ID, err.Error(), true)
		return
	}
	r.logger.Warning("runner", "operation %s failed verification; rolling back once to %s", operation.ID, current)
	if rollbackError := r.rollback(ctx, current); rollbackError != nil {
		message := fmt.Sprintf("update failed: %v; rollback failed: %v", updateError, rollbackError)
		_ = r.store.FinishFailure(operation.ID, message, true)
		r.logger.Error("runner", "operation %s halted after rollback failure", operation.ID)
		return
	}
	message := fmt.Sprintf("update failed and was rolled back to %s: %v", current, updateError)
	_ = r.store.FinishFailure(operation.ID, message, false)
	r.logger.Warning("runner", "operation %s rolled back to %s", operation.ID, current)
}

func (r *Runner) rollback(ctx context.Context, target string) error {
	if err := r.waitForDockerWithoutState(ctx); err != nil {
		return err
	}
	images := Images(r.namespace, target)
	if err := r.pullAll(ctx, images); err != nil {
		return err
	}
	if err := r.compose.Recreate(ctx, images); err != nil {
		return err
	}
	verifyContext, cancel := context.WithTimeout(ctx, r.healthTimeout)
	defer cancel()
	return r.verifier.Verify(verifyContext, target)
}

func (r *Runner) waitForDockerWithoutState(ctx context.Context) error {
	started := time.Now()
	for {
		pingContext, cancel := context.WithTimeout(ctx, 5*time.Second)
		err := r.docker.Ping(pingContext)
		cancel()
		if err == nil {
			return nil
		}
		if r.dockerRetryMax > 0 && time.Since(started) >= r.dockerRetryMax {
			return err
		}
		timer := time.NewTimer(r.dockerRetry)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}

func (r *Runner) finishBeforeRecreate(operationID string, err error) {
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		_ = r.store.FinishInterrupted(operationID, "operation interrupted; submit a new request")
	} else {
		_ = r.store.FinishFailure(operationID, err.Error(), false)
	}
	r.logger.Error("runner", "operation %s stopped before recreate: %v", operationID, err)
}
