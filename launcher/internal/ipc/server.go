package ipc

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"strings"
	"sync"

	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/auth"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/coordinator"
	"github.com/turbo3000r/DiscordCombatAI/launcher/internal/state"
)

const maxBodyBytes = 16 * 1024

type OperationRunner interface {
	Run(context.Context, *state.Operation)
}

type Handler struct {
	auth        *auth.Authenticator
	coordinator *coordinator.Coordinator
	runner      OperationRunner
	runContext  context.Context
	runs        sync.WaitGroup
}

func NewHandler(
	authenticator *auth.Authenticator,
	coord *coordinator.Coordinator,
	runner OperationRunner,
	runContext context.Context,
) *Handler {
	return &Handler{auth: authenticator, coordinator: coord, runner: runner, runContext: runContext}
}

func (h *Handler) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	switch request.URL.Path {
	case "/v1/update":
		if request.Method != http.MethodPost {
			writeError(writer, http.StatusMethodNotAllowed, "", "method_not_allowed", "Method not allowed.")
			return
		}
		h.update(writer, request)
	case "/v1/status":
		if request.Method != http.MethodGet {
			writeError(writer, http.StatusMethodNotAllowed, "", "method_not_allowed", "Method not allowed.")
			return
		}
		h.status(writer, request)
	default:
		writeError(writer, http.StatusNotFound, "", "not_found", "Not found.")
	}
}

func (h *Handler) update(writer http.ResponseWriter, request *http.Request) {
	mediaType, _, err := mime.ParseMediaType(request.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		writeError(writer, http.StatusBadRequest, "", "invalid_request", "Invalid request.")
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(writer, request.Body, maxBodyBytes))
	if err != nil {
		writeError(writer, http.StatusRequestEntityTooLarge, "", "request_too_large", "Request body is too large.")
		return
	}
	headerID, err := h.auth.Authenticate(request, body, true)
	if err != nil {
		writeError(writer, http.StatusUnauthorized, "", "unauthorized", "Unauthorized.")
		return
	}
	var update coordinator.UpdateRequest
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&update); err != nil {
		writeError(writer, http.StatusBadRequest, headerID, "invalid_request", "Invalid request.")
		return
	}
	if err := ensureEOF(decoder); err != nil || update.RequestID != headerID {
		writeError(writer, http.StatusBadRequest, headerID, "invalid_request", "Invalid request.")
		return
	}
	admission, err := h.coordinator.Submit(update, state.Digest(body))
	if err != nil {
		switch {
		case errors.Is(err, coordinator.ErrMalformed):
			writeError(writer, http.StatusBadRequest, headerID, "invalid_request", "Invalid request.")
		case errors.Is(err, coordinator.ErrUnprocessable):
			writeError(writer, http.StatusUnprocessableEntity, headerID, "unsupported_update", "Unsupported update request.")
		case errors.Is(err, state.ErrRequestIntegrity):
			writeError(writer, http.StatusUnauthorized, "", "unauthorized", "Unauthorized.")
		default:
			writeError(writer, http.StatusInternalServerError, headerID, "internal_error", "Launcher could not persist the request.")
		}
		return
	}
	if admission.Kind == state.AdmissionBusy {
		writeJSON(writer, http.StatusConflict, struct {
			SchemaVersion int       `json:"schema_version"`
			RequestID     string    `json:"request_id"`
			Error         errorBody `json:"error"`
			OperationID   string    `json:"operation_id"`
			TargetVersion string    `json:"target_version"`
		}{
			SchemaVersion: 1, RequestID: headerID,
			Error:       errorBody{SchemaVersion: 1, Code: "update_busy", Message: "Launcher is processing another target."},
			OperationID: admission.Operation.ID, TargetVersion: admission.Operation.TargetVersion,
		})
		return
	}
	writeJSON(writer, admission.Status, admission.Response)
	if admission.Kind == state.AdmissionStarted && admission.Operation != nil {
		h.runs.Add(1)
		go func() {
			defer h.runs.Done()
			h.runner.Run(h.runContext, admission.Operation)
		}()
	}
}

func (h *Handler) Wait(ctx context.Context) error {
	done := make(chan struct{})
	go func() {
		defer close(done)
		h.runs.Wait()
	}()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (h *Handler) status(writer http.ResponseWriter, request *http.Request) {
	if request.ContentLength > 0 {
		writeError(writer, http.StatusBadRequest, "", "invalid_request", "Invalid request.")
		return
	}
	requestID, err := h.auth.Authenticate(request, nil, false)
	if err != nil {
		writeError(writer, http.StatusUnauthorized, "", "unauthorized", "Unauthorized.")
		return
	}
	status, err := h.coordinator.Status()
	if err != nil {
		writeError(writer, http.StatusInternalServerError, requestID, "internal_error", "Launcher state is unavailable.")
		return
	}
	writeJSON(writer, http.StatusOK, status)
}

type errorBody struct {
	SchemaVersion int    `json:"schema_version"`
	Code          string `json:"code"`
	Message       string `json:"message"`
}

func writeError(writer http.ResponseWriter, status int, requestID, code, message string) {
	writeJSON(writer, status, struct {
		SchemaVersion int       `json:"schema_version"`
		RequestID     string    `json:"request_id,omitempty"`
		Error         errorBody `json:"error"`
	}{
		SchemaVersion: 1, RequestID: requestID,
		Error: errorBody{SchemaVersion: 1, Code: code, Message: message},
	})
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.Header().Set("Cache-Control", "no-store")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}

func ensureEOF(decoder *json.Decoder) error {
	var extra any
	err := decoder.Decode(&extra)
	if errors.Is(err, io.EOF) {
		return nil
	}
	if err == nil && extra == nil {
		return errors.New("unexpected trailing JSON")
	}
	return err
}

func RedactedHeaders(headers http.Header) http.Header {
	copy := headers.Clone()
	for name := range copy {
		switch strings.ToLower(name) {
		case strings.ToLower(auth.TimestampHeader), strings.ToLower(auth.RequestIDHeader), strings.ToLower(auth.SignatureHeader), "authorization":
			copy.Set(name, "[REDACTED]")
		}
	}
	return copy
}
