//go:build linux

package main

import (
	"errors"
	"testing"

	"github.com/chx739/sandboxd/internal/workspace"
)

func TestStorageFailureIsNotPolicyDenial(t *testing.T) {
	code, body := fileFailure(&workspace.ExecutionError{Reason: "directory sync failed", OutcomeUncertain: true})
	if code != 3 || body["sandboxRecovery"] != true || body["outcomeUncertain"] != true || body["denyLayer"] != nil {
		t.Fatal(code, body)
	}
	code, body = fileFailure(errors.New("CAS mismatch"))
	if code != 2 || body["denyLayer"] != "workspace-policy" {
		t.Fatal(code, body)
	}
}
