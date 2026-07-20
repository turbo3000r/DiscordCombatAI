package engine

import (
	"strings"
	"testing"
)

func TestConsumePullResponseDetectsStreamError(t *testing.T) {
	t.Parallel()
	success := "{\"status\":\"Pulling fs layer\"}\n{\"status\":\"Download complete\"}\n"
	if err := consumePullResponse(strings.NewReader(success)); err != nil {
		t.Fatalf("success stream rejected: %v", err)
	}
	failure := "{\"status\":\"Pulling fs layer\"}\n{\"errorDetail\":{\"message\":\"unauthorized\"},\"error\":\"unauthorized\"}\n"
	if err := consumePullResponse(strings.NewReader(failure)); err == nil {
		t.Fatal("Docker stream error was ignored")
	}
}
