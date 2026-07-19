package engine

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/auth"
)

func TestHeadVerifierRequiresAuthenticatedAliveExactVersion(t *testing.T) {
	t.Parallel()
	secret := []byte("01234567890123456789012345678901")
	authenticator := auth.New(secret, nil)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if _, err := authenticator.Authenticate(request, nil, false); err != nil {
			writer.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"schema_version": 1,
			"status":         "alive",
			"version":        "v1.2.3",
			"instance_id":    "6b44781e-40f8-4807-9b4b-9087430c14b6",
			"started_at":     "2026-07-15T17:06:00Z",
		})
	}))
	defer server.Close()
	verifier := NewHeadVerifier(server.URL, time.Millisecond, authenticator)

	if err := verifier.Verify(context.Background(), "v1.2.3"); err != nil {
		t.Fatalf("matching health rejected: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Millisecond)
	defer cancel()
	if err := verifier.Verify(ctx, "v1.2.4"); err == nil {
		t.Fatal("mismatched version accepted")
	}
}
