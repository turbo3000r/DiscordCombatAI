package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type Compose interface {
	Recreate(context.Context, map[string]string) error
}

type CommandRunner interface {
	Run(context.Context, string, []string, string, []string) ([]byte, error)
}

type ExecRunner struct{}

func (ExecRunner) Run(ctx context.Context, executable string, args []string, dir string, environment []string) ([]byte, error) {
	command := exec.CommandContext(ctx, executable, args...)
	command.Dir = dir
	command.Env = environment
	return command.CombinedOutput()
}

type ComposeCLI struct {
	composeFile string
	override    string
	project     string
	runner      CommandRunner
}

func NewComposeCLI(composeFile, statePath string, runner CommandRunner) *ComposeCLI {
	return NewComposeCLIWithProject(composeFile, statePath, "discordcombatai", runner)
}

func NewComposeCLIWithProject(composeFile, statePath, project string, runner CommandRunner) *ComposeCLI {
	if runner == nil {
		runner = ExecRunner{}
	}
	return &ComposeCLI{
		composeFile: composeFile,
		override:    filepath.Join(filepath.Dir(statePath), "launcher-compose.override.json"),
		project:     project,
		runner:      runner,
	}
}

func (c *ComposeCLI) Recreate(ctx context.Context, images map[string]string) error {
	if err := validateImageSet(images); err != nil {
		return err
	}
	namespace, target, err := imageCoordinates(images)
	if err != nil {
		return err
	}
	override := struct {
		Services map[string]struct {
			Image string `json:"image"`
		} `json:"services"`
	}{Services: make(map[string]struct {
		Image string `json:"image"`
	}, len(ApplicationServices))}
	for _, service := range ApplicationServices {
		override.Services[service] = struct {
			Image string `json:"image"`
		}{Image: images[service]}
	}
	raw, err := json.MarshalIndent(override, "", "  ")
	if err != nil {
		return err
	}
	if err := writeRuntimeFile(c.override, append(raw, '\n')); err != nil {
		return err
	}
	args := []string{
		"compose", "--project-name", c.project, "--file", c.composeFile, "--file", c.override,
		"up", "-d", "--force-recreate", "--no-build", "--no-deps", "--pull", "never",
	}
	args = append(args, ApplicationServices...)
	output, err := c.runner.Run(
		ctx,
		"docker",
		args,
		filepath.Dir(c.composeFile),
		composeEnvironment(namespace, target),
	)
	if err != nil {
		return fmt.Errorf("docker compose recreate failed: %w: %s", err, safeOutput(output))
	}
	return nil
}

func validateImageSet(images map[string]string) error {
	if len(images) != len(ApplicationServices) {
		return fmt.Errorf("image set must contain exactly head, bot, and ai_worker")
	}
	for _, service := range ApplicationServices {
		if strings.TrimSpace(images[service]) == "" {
			return fmt.Errorf("image for %s is missing", service)
		}
	}
	return nil
}

func imageCoordinates(images map[string]string) (string, string, error) {
	head := images["head"]
	marker := "/head:"
	index := strings.LastIndex(head, marker)
	if index <= 0 || index+len(marker) >= len(head) {
		return "", "", fmt.Errorf("head image does not use namespace/head:tag")
	}
	namespace := head[:index]
	target := head[index+len(marker):]
	for _, service := range ApplicationServices {
		expected := namespace + "/" + service + ":" + target
		if images[service] != expected {
			return "", "", fmt.Errorf("image for %s does not match coordinated namespace and target", service)
		}
	}
	return namespace, target, nil
}

func composeEnvironment(namespace, target string) []string {
	environment := make([]string, 0, len(os.Environ()))
	for _, entry := range os.Environ() {
		name := strings.SplitN(entry, "=", 2)[0]
		switch strings.ToUpper(name) {
		case "LAUNCHER_GHCR_TOKEN", "LAUNCHER_GHCR_NAMESPACE", "APPLICATION_VERSION",
			"DCA_HEAD_IMAGE", "DCA_BOT_IMAGE", "DCA_AI_WORKER_IMAGE":
			continue
		default:
			environment = append(environment, entry)
		}
	}
	environment = append(
		environment,
		"LAUNCHER_GHCR_NAMESPACE="+namespace,
		"APPLICATION_VERSION="+target,
	)
	return environment
}

func safeOutput(output []byte) string {
	const limit = 2048
	text := strings.TrimSpace(string(output))
	if len(text) > limit {
		text = text[:limit] + "..."
	}
	return text
}

func writeRuntimeFile(path string, body []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".compose-override-*")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer os.Remove(name)
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(body); err != nil {
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
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return err
	}
	return os.Rename(name, path)
}
