package logging

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoggerRedactsSecretsAndRotates(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "launcher.log")
	logger, err := New(path, 128, 2, "super-secret-token")
	if err != nil {
		t.Fatal(err)
	}
	logger.Error("test", "registry rejected super-secret-token")
	for i := 0; i < 10; i++ {
		logger.Info("test", "padding padding padding padding %d", i)
	}
	if err := logger.Close(); err != nil {
		t.Fatal(err)
	}
	matches, err := filepath.Glob(path + "*")
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) < 2 {
		t.Fatalf("expected rotation, files = %#v", matches)
	}
	for _, match := range matches {
		raw, err := os.ReadFile(match)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(raw), "super-secret-token") {
			t.Fatalf("secret found in %s", match)
		}
	}
}
