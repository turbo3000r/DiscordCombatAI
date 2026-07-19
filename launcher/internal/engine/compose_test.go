package engine

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestComposeUsesArgumentArrayAndFixedImageSet(t *testing.T) {
	t.Parallel()
	directory := t.TempDir()
	composeFile := filepath.Join(directory, "compose file.yml")
	if err := os.WriteFile(composeFile, []byte("services: {}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runner := &captureCommand{}
	compose := NewComposeCLI(composeFile, filepath.Join(directory, "state.json"), runner)
	images := Images("ghcr.io/acme/discord-combat-ai", "v1.2.3")

	if err := compose.Recreate(context.Background(), images); err != nil {
		t.Fatal(err)
	}

	expected := []string{
		"compose", "--project-name", "discordcombatai",
		"--file", composeFile, "--file", filepath.Join(directory, "launcher-compose.override.json"),
		"up", "-d", "--force-recreate", "--no-build", "--no-deps", "--pull", "never",
		"head", "bot", "ai_worker",
	}
	if runner.executable != "docker" || !reflect.DeepEqual(runner.args, expected) {
		t.Fatalf("command = %s %#v", runner.executable, runner.args)
	}
	raw, err := os.ReadFile(filepath.Join(directory, "launcher-compose.override.json"))
	if err != nil {
		t.Fatal(err)
	}
	for service, image := range images {
		if !strings.Contains(string(raw), `"`+service+`"`) || !strings.Contains(string(raw), image) {
			t.Fatalf("override does not pin %s to %s: %s", service, image, raw)
		}
	}
	assertEnvironmentValue(t, runner.environment, "LAUNCHER_GHCR_NAMESPACE", "ghcr.io/acme/discord-combat-ai")
	assertEnvironmentValue(t, runner.environment, "APPLICATION_VERSION", "v1.2.3")
}

func TestComposeRejectsIncompleteImageSet(t *testing.T) {
	t.Parallel()
	compose := NewComposeCLI("compose.yml", filepath.Join(t.TempDir(), "state.json"), &captureCommand{})
	if err := compose.Recreate(context.Background(), map[string]string{"head": "example"}); err == nil {
		t.Fatal("incomplete image set was accepted")
	}
}

func TestComposeUsesConfiguredProjectAndRejectsMixedTarget(t *testing.T) {
	t.Parallel()
	directory := t.TempDir()
	composeFile := filepath.Join(directory, "compose.yml")
	if err := os.WriteFile(composeFile, []byte("services: {}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runner := &captureCommand{}
	compose := NewComposeCLIWithProject(composeFile, filepath.Join(directory, "state.json"), "dca_test", runner)
	images := Images("ghcr.io/acme/discord-combat-ai", "v1.2.3")
	images["bot"] = "ghcr.io/acme/discord-combat-ai/bot:v1.2.4"

	if err := compose.Recreate(context.Background(), images); err == nil {
		t.Fatal("mixed image target was accepted")
	}
	if runner.executable != "" {
		t.Fatal("docker compose ran for a mixed image target")
	}

	images = Images("ghcr.io/acme/discord-combat-ai", "v1.2.3")
	if err := compose.Recreate(context.Background(), images); err != nil {
		t.Fatal(err)
	}
	if len(runner.args) < 3 || runner.args[2] != "dca_test" {
		t.Fatalf("configured project missing from args: %#v", runner.args)
	}
}

func assertEnvironmentValue(t *testing.T, environment []string, name, expected string) {
	t.Helper()
	prefix := name + "="
	for _, entry := range environment {
		if strings.HasPrefix(entry, prefix) {
			if value := strings.TrimPrefix(entry, prefix); value != expected {
				t.Fatalf("%s = %q, want %q", name, value, expected)
			}
			return
		}
	}
	t.Fatalf("%s was not injected", name)
}

type captureCommand struct {
	executable  string
	args        []string
	dir         string
	environment []string
}

func (c *captureCommand) Run(_ context.Context, executable string, args []string, dir string, environment []string) ([]byte, error) {
	c.executable = executable
	c.args = append([]string(nil), args...)
	c.dir = dir
	c.environment = append([]string(nil), environment...)
	return nil, nil
}
