package config

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestLoadResolvesHostPathsRelativeToExecutable(t *testing.T) {
	t.Setenv("LAUNCHER_COMPOSE_FILE", "config/compose.yml")
	t.Setenv("LAUNCHER_VERSION_HISTORY_PATH", "state/launcher.json")
	t.Setenv("LAUNCHER_LOCK_PATH", "locks/coordinator.lock")
	t.Setenv("LAUNCHER_LOG_PATH", "logs/launcher.log")
	t.Setenv("LAUNCHER_IPC_SECRET_FILE", "secrets/ipc.key")
	t.Setenv("LAUNCHER_COMPOSE_PROJECT", "dca_node_1")

	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	base := filepath.Dir(executable)
	expected := map[string]string{
		"compose": filepath.Join(base, "config", "compose.yml"),
		"state":   filepath.Join(base, "state", "launcher.json"),
		"lock":    filepath.Join(base, "locks", "coordinator.lock"),
		"log":     filepath.Join(base, "logs", "launcher.log"),
		"secret":  filepath.Join(base, "secrets", "ipc.key"),
	}
	actual := map[string]string{
		"compose": cfg.ComposeFile,
		"state":   cfg.StatePath,
		"lock":    cfg.LockPath,
		"log":     cfg.LogPath,
		"secret":  cfg.SecretFile,
	}
	for name, want := range expected {
		if actual[name] != want {
			t.Fatalf("%s path = %q, want %q", name, actual[name], want)
		}
	}
	if cfg.ComposeProject != "dca_node_1" {
		t.Fatalf("ComposeProject = %q", cfg.ComposeProject)
	}
}

func TestLoadSecretPreservesExactBytes(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "secret")
	secret := append(bytes.Repeat([]byte{0x5a}, minimumSecretBytes), '\r', '\n', 0)
	if err := os.WriteFile(path, secret, 0o600); err != nil {
		t.Fatal(err)
	}
	loaded, err := (Config{SecretFile: path}).LoadSecret()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(loaded, secret) {
		t.Fatalf("secret bytes changed: got %x, want %x", loaded, secret)
	}
}

func TestComposeProjectValidation(t *testing.T) {
	t.Parallel()
	for _, project := range []string{"discordcombatai", "dca-node_1", "1node"} {
		if !validComposeProject(project) {
			t.Fatalf("valid project %q rejected", project)
		}
	}
	for _, project := range []string{"", "-node", "DCA", "dca.node", "dca node"} {
		if validComposeProject(project) {
			t.Fatalf("invalid project %q accepted", project)
		}
	}
}
