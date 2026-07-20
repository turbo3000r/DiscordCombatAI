package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/auth"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/config"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/coordinator"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/engine"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/ipc"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/logging"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/state"
)

func Execute(args []string) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	if len(args) == 0 {
		return daemon(cfg)
	}
	switch args[0] {
	case "daemon":
		if len(args) != 1 {
			return errors.New("daemon takes no arguments")
		}
		return daemon(cfg)
	case "status":
		if len(args) != 1 {
			return errors.New("status takes no arguments")
		}
		return printStatus(cfg)
	case "update":
		return update(cfg, args[1:])
	case "rollback":
		if len(args) != 1 {
			return errors.New("rollback takes no arguments")
		}
		return rollback(cfg)
	default:
		return fmt.Errorf("unknown command %q; expected daemon, update, rollback, or status", args[0])
	}
}

func daemon(cfg config.Config) error {
	if err := cfg.ValidateDaemon(); err != nil {
		return err
	}
	resources, err := buildRuntime(cfg)
	if err != nil {
		return err
	}
	defer resources.Close()
	runContext, cancelRuns := context.WithCancel(context.Background())
	defer cancelRuns()
	handler := ipc.NewHandler(resources.authenticator, resources.coordinator, resources.runner, runContext)
	server := &http.Server{
		Addr:              cfg.BindAddress,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	errChannel := make(chan error, 1)
	go func() {
		resources.logger.Info("daemon", "listening on %s", cfg.BindAddress)
		errChannel <- server.ListenAndServe()
	}()
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(signals)
	select {
	case serverError := <-errChannel:
		cancelRuns()
		waitContext, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
		defer cancel()
		waitError := handler.Wait(waitContext)
		if errors.Is(serverError, http.ErrServerClosed) {
			return waitError
		}
		return errors.Join(serverError, waitError)
	case <-signals:
		cancelRuns()
		shutdownContext, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
		defer cancel()
		shutdownError := server.Shutdown(shutdownContext)
		waitError := handler.Wait(shutdownContext)
		return errors.Join(shutdownError, waitError)
	}
}

func update(cfg config.Config, args []string) error {
	flags := flag.NewFlagSet("update", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	target := flags.String("version", "", "target application version")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || *target == "" {
		return errors.New("usage: launcher update --version vMAJOR.MINOR.PATCH[-PRERELEASE]")
	}
	resources, err := buildRuntime(cfg)
	if err != nil {
		return err
	}
	defer resources.Close()
	requestID, err := auth.NewRequestID()
	if err != nil {
		return err
	}
	request := coordinator.UpdateRequest{
		SchemaVersion: 1, RequestID: requestID, TargetVersion: *target, Reason: "manual",
		RequestedAt: time.Now().UTC().Format(time.RFC3339), SourceNodeID: "launcher-cli",
	}
	return runCLI(resources, request)
}

func rollback(cfg config.Config) error {
	resources, err := buildRuntime(cfg)
	if err != nil {
		return err
	}
	defer resources.Close()
	requestID, err := auth.NewRequestID()
	if err != nil {
		return err
	}
	request, err := resources.coordinator.RollbackRequest(requestID, time.Now())
	if err != nil {
		return err
	}
	return runCLI(resources, request)
}

func runCLI(resources *runtimeResources, request coordinator.UpdateRequest) error {
	body, err := json.Marshal(request)
	if err != nil {
		return err
	}
	admission, err := resources.coordinator.Submit(request, state.Digest(body))
	if err != nil {
		return err
	}
	switch admission.Kind {
	case state.AdmissionBusy:
		return fmt.Errorf("update_busy: operation %s is processing %s", admission.Operation.ID, admission.Operation.TargetVersion)
	case state.AdmissionStarted:
		resources.runner.Run(context.Background(), admission.Operation)
		status, err := resources.coordinator.Status()
		if err != nil {
			return err
		}
		if status.State != state.OperationState("IDLE") {
			return fmt.Errorf("operation ended in %s: %s", status.State, status.LastError)
		}
		return printJSON(status)
	default:
		return printJSON(admission.Response)
	}
}

func printStatus(cfg config.Config) error {
	store, err := state.OpenWithLock(cfg.StatePath, cfg.LockPath, cfg.RequestRetention, cfg.ReplayRetention)
	if err != nil {
		return err
	}
	status, err := store.Status()
	if err != nil {
		return err
	}
	return printJSON(status)
}

func printJSON(value any) error {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	return encoder.Encode(value)
}

type runtimeResources struct {
	logger        *logging.Logger
	docker        engine.Docker
	authenticator *auth.Authenticator
	coordinator   *coordinator.Coordinator
	runner        *engine.Runner
}

func buildRuntime(cfg config.Config) (*runtimeResources, error) {
	if err := cfg.ValidateDaemon(); err != nil {
		return nil, err
	}
	secret, err := cfg.LoadSecret()
	if err != nil {
		return nil, err
	}
	logger, err := logging.New(cfg.LogPath, cfg.LogMaxBytes, cfg.LogBackups, cfg.GHCRToken, string(secret))
	if err != nil {
		return nil, err
	}
	store, err := state.OpenWithLock(cfg.StatePath, cfg.LockPath, cfg.RequestRetention, cfg.ReplayRetention)
	if err != nil {
		logger.Close()
		return nil, err
	}
	dockerClient, err := engine.NewDockerClient(cfg.GHCRNamespace, cfg.GHCRToken)
	if err != nil {
		logger.Close()
		return nil, err
	}
	authenticator := auth.New(secret, store)
	coord := coordinator.New(store)
	compose := engine.NewComposeCLIWithProject(
		cfg.ComposeFile,
		cfg.StatePath,
		cfg.ComposeProject,
		nil,
	)
	verifier := engine.NewHeadVerifier(cfg.HeadHealthURL, cfg.HealthInterval, authenticator)
	runner := engine.NewRunner(
		dockerClient, compose, verifier, store, logger, cfg.GHCRNamespace,
		cfg.DockerRetry, cfg.DockerRetryMax, cfg.HealthTimeout,
	)
	return &runtimeResources{
		logger: logger, docker: dockerClient, authenticator: authenticator,
		coordinator: coord, runner: runner,
	}, nil
}

func (r *runtimeResources) Close() {
	if r.docker != nil {
		_ = r.docker.Close()
	}
	if r.logger != nil {
		_ = r.logger.Close()
	}
}
