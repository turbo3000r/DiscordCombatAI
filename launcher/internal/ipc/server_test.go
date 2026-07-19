package ipc

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/auth"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/coordinator"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/state"
)

func TestUpdateAdmissionMatrixAndAuthenticatedStatus(t *testing.T) {
	t.Parallel()
	secret := []byte("01234567890123456789012345678901")
	store, err := state.Open(filepath.Join(t.TempDir(), "state.json"), 24*time.Hour, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	authenticator := auth.New(secret, store)
	handler := NewHandler(authenticator, coordinator.New(store), noopRunner{}, context.Background())

	unauthorized := httptest.NewRequest(http.MethodPost, "/v1/update", bytes.NewReader([]byte(`{}`)))
	unauthorized.Header.Set("Content-Type", "application/json")
	assertStatus(t, handler, unauthorized, http.StatusUnauthorized)

	malformedID := newTestID(t)
	malformed := signedUpdate(t, authenticator, malformedID, []byte(`{"schema_version":1}`))
	assertStatus(t, handler, malformed, http.StatusBadRequest)

	invalidID := newTestID(t)
	invalid := validBody(t, invalidID, "not-a-version", "manual")
	assertStatus(t, handler, signedUpdate(t, authenticator, invalidID, invalid), http.StatusUnprocessableEntity)

	firstID := newTestID(t)
	firstBody := validBody(t, firstID, "v1.2.3", "manual")
	firstRecorder := assertStatus(t, handler, signedUpdate(t, authenticator, firstID, firstBody), http.StatusAccepted)
	var first state.UpdateResponse
	if err := json.Unmarshal(firstRecorder.Body.Bytes(), &first); err != nil {
		t.Fatal(err)
	}

	sameID := newTestID(t)
	sameBody := validBody(t, sameID, "v1.2.3", "manual")
	sameRecorder := assertStatus(t, handler, signedUpdate(t, authenticator, sameID, sameBody), http.StatusAccepted)
	var same state.UpdateResponse
	if err := json.Unmarshal(sameRecorder.Body.Bytes(), &same); err != nil {
		t.Fatal(err)
	}
	if same.OperationID != first.OperationID {
		t.Fatalf("same target operation = %s, want %s", same.OperationID, first.OperationID)
	}

	conflictID := newTestID(t)
	conflictBody := validBody(t, conflictID, "v1.2.4", "manual")
	assertStatus(t, handler, signedUpdate(t, authenticator, conflictID, conflictBody), http.StatusConflict)

	if err := store.FinishSuccess(first.OperationID); err != nil {
		t.Fatal(err)
	}
	currentID := newTestID(t)
	currentBody := validBody(t, currentID, "v1.2.3", "manual")
	assertStatus(t, handler, signedUpdate(t, authenticator, currentID, currentBody), http.StatusOK)

	statusRequest := httptest.NewRequest(http.MethodGet, "/v1/status", nil)
	statusID := newTestID(t)
	if err := authenticator.SignRequest(statusRequest, nil, statusID, time.Now()); err != nil {
		t.Fatal(err)
	}
	statusRecorder := assertStatus(t, handler, statusRequest, http.StatusOK)
	if !bytes.Contains(statusRecorder.Body.Bytes(), []byte(`"current_version":"v1.2.3"`)) {
		t.Fatalf("status body = %s", statusRecorder.Body.String())
	}
}

func TestAutomaticPrereleaseRejectedButManualAccepted(t *testing.T) {
	t.Parallel()
	secret := []byte("01234567890123456789012345678901")
	store, err := state.Open(filepath.Join(t.TempDir(), "state.json"), 24*time.Hour, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	authenticator := auth.New(secret, store)
	handler := NewHandler(authenticator, coordinator.New(store), noopRunner{}, context.Background())

	automaticID := newTestID(t)
	automatic := validBody(t, automaticID, "v2.0.0-rc.1", "auto_detected")
	assertStatus(t, handler, signedUpdate(t, authenticator, automaticID, automatic), http.StatusUnprocessableEntity)

	manualID := newTestID(t)
	manual := validBody(t, manualID, "v2.0.0-rc.1", "manual")
	assertStatus(t, handler, signedUpdate(t, authenticator, manualID, manual), http.StatusAccepted)
}

func validBody(t *testing.T, requestID, target, reason string) []byte {
	t.Helper()
	body, err := json.Marshal(coordinator.UpdateRequest{
		SchemaVersion: 1, RequestID: requestID, TargetVersion: target, Reason: reason,
		RequestedAt: time.Now().UTC().Format(time.RFC3339), SourceNodeID: "node-a",
	})
	if err != nil {
		t.Fatal(err)
	}
	return body
}

func signedUpdate(t *testing.T, authenticator *auth.Authenticator, requestID string, body []byte) *http.Request {
	t.Helper()
	request := httptest.NewRequest(http.MethodPost, "/v1/update", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	if err := authenticator.SignRequest(request, body, requestID, time.Now()); err != nil {
		t.Fatal(err)
	}
	return request
}

func assertStatus(t *testing.T, handler http.Handler, request *http.Request, expected int) *httptest.ResponseRecorder {
	t.Helper()
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != expected {
		t.Fatalf("%s %s status = %d, want %d; body=%s", request.Method, request.URL, recorder.Code, expected, recorder.Body.String())
	}
	return recorder
}

func newTestID(t *testing.T) string {
	t.Helper()
	id, err := auth.NewRequestID()
	if err != nil {
		t.Fatal(err)
	}
	return id
}

type noopRunner struct{}

func (noopRunner) Run(context.Context, *state.Operation) {}

func TestBodyLimit(t *testing.T) {
	t.Parallel()
	secret := []byte("01234567890123456789012345678901")
	store, err := state.Open(filepath.Join(t.TempDir(), "state.json"), 24*time.Hour, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(auth.New(secret, store), coordinator.New(store), noopRunner{}, context.Background())
	request := httptest.NewRequest(http.MethodPost, "/v1/update", bytes.NewReader(bytes.Repeat([]byte("x"), maxBodyBytes+1)))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Content-Length", strconv.Itoa(maxBodyBytes+1))
	assertStatus(t, handler, request, http.StatusRequestEntityTooLarge)
}
