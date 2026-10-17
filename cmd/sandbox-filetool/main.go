//go:build linux

// sandbox-filetool accepts bounded JSON, or the trusted startup-only --check
// probe. There is no free command, workspace root or path command-line option.
package main

import (
	"encoding/json"
	"errors"
	"os"

	"github.com/chx739/sandboxd/internal/workspace"
)

func main() {
	if len(os.Args) == 2 && os.Args[1] == "--check" {
		if err := workspace.Check("/workspace"); err != nil {
			fail(err.Error())
		}
		json.NewEncoder(os.Stdout).Encode(map[string]bool{"writable": true})
		return
	}
	if len(os.Args) != 1 {
		fail("arguments are not supported")
	}
	req, err := workspace.Decode(os.Stdin)
	if err != nil {
		fail(err.Error())
	}
	result, err := workspace.Execute("/workspace", req)
	if err != nil {
		code, body := fileFailure(err)
		json.NewEncoder(os.Stdout).Encode(body)
		os.Exit(code)
	}
	data, err := json.Marshal(result)
	if err != nil || len(data) > workspace.MaxResponseBytes {
		fail("file response exceeds limit")
	}
	os.Stdout.Write(append(data, '\n'))
}

func fileFailure(err error) (int, map[string]any) {
	var execution *workspace.ExecutionError
	if errors.As(err, &execution) {
		return 3, map[string]any{"error": execution.Error(), "errorType": "workspace-execution", "sandboxRecovery": true, "outcomeUncertain": execution.OutcomeUncertain}
	}
	return 2, map[string]any{"denyLayer": "workspace-policy", "error": err.Error()}
}

func fail(message string) {
	json.NewEncoder(os.Stdout).Encode(map[string]any{"denyLayer": "workspace-policy", "error": message})
	os.Exit(2)
}
