package auth

import (
	"errors"
	"net/http"
	"net/url"
	"strconv"
	"testing"
	"time"
)

const goldenRequestID = "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca"

func TestPythonGoldenVector(t *testing.T) {
	t.Parallel()
	canonical, err := Canonical("post", "/v1/update", 1721053200, goldenRequestID, []byte("hello"))
	if err != nil {
		t.Fatal(err)
	}
	expectedCanonical := "POST\n/v1/update\n1721053200\n02e75c7a-8de1-4e3b-883a-5c41ae8b98ca\n2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
	if canonical != expectedCanonical {
		t.Fatalf("canonical mismatch:\n%s", canonical)
	}
	signature, err := Sign([]byte("secret"), "POST", "/v1/update", 1721053200, goldenRequestID, []byte("hello"))
	if err != nil {
		t.Fatal(err)
	}
	if signature != "2984b1dffdbb42554922746130b1e9f6fb285f86702437377ed06c9c74f39e8d" {
		t.Fatalf("signature = %s", signature)
	}
}

func TestAuthenticateExactBytesSkewAndReplay(t *testing.T) {
	t.Parallel()
	secret := []byte("01234567890123456789012345678901")
	replays := &replayFake{}
	authenticator := New(secret, replays)
	authenticator.now = func() time.Time { return time.Unix(1721053200, 0).UTC() }

	request := signedRequest(t, authenticator, []byte("{\"x\":1}"), 1721053200)
	if _, err := authenticator.Authenticate(request, []byte("{\"x\":1}"), true); err != nil {
		t.Fatalf("valid request rejected: %v", err)
	}
	request = signedRequest(t, authenticator, []byte("{\"x\":1}"), 1721053200)
	if _, err := authenticator.Authenticate(request, []byte("{ \"x\": 1 }"), true); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("changed exact bytes error = %v", err)
	}
	request = signedRequest(t, authenticator, []byte("{\"x\":1}"), 1721053169)
	if _, err := authenticator.Authenticate(request, []byte("{\"x\":1}"), true); !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("skewed request error = %v", err)
	}
}

func signedRequest(t *testing.T, authenticator *Authenticator, body []byte, timestamp int64) *http.Request {
	t.Helper()
	request := &http.Request{
		Method: http.MethodPost,
		URL:    &url.URL{Path: "/v1/update"},
		Header: make(http.Header),
	}
	signature, err := Sign(authenticator.secret, request.Method, request.URL.RequestURI(), timestamp, goldenRequestID, body)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set(TimestampHeader, strconv.FormatInt(timestamp, 10))
	request.Header.Set(RequestIDHeader, goldenRequestID)
	request.Header.Set(SignatureHeader, signature)
	return request
}

type replayFake struct {
	signature string
	digest    string
}

func (r *replayFake) CheckReplay(_ string, signature, digest, _ string, allowIdentical bool) (bool, error) {
	if r.signature == "" {
		r.signature, r.digest = signature, digest
		return false, nil
	}
	if r.signature == signature && r.digest == digest && allowIdentical {
		return true, nil
	}
	return false, errors.New("replay")
}
