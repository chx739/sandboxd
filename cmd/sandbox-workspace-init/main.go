//go:build linux

// Trusted maintenance entrypoint; never exposed as an Agent tool or generic exec.
package main

import (
	"encoding/json"
	"os"

	"github.com/chx739/sandboxd/internal/workspace"
)

func main() {
	if len(os.Args) != 2 {
		fail("one workspace identity is required")
	}
	result, err := workspace.Initialize("/workspace-root", os.Args[1])
	if err != nil {
		fail(err.Error())
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		os.Exit(1)
	}
}

func fail(message string) {
	json.NewEncoder(os.Stdout).Encode(map[string]string{"error": message})
	os.Exit(2)
}
