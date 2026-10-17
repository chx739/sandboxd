//go:build linux

// sandbox-filetool accepts one bounded JSON request on stdin and exits. The
// workspace root and operations cannot be changed by command-line arguments.
package main

import (
	"encoding/json"
	"os"

	"github.com/chx739/sandboxd/internal/workspace"
)

func main() {
	if len(os.Args) != 1 {
		fail("arguments are not supported")
	}
	req, err := workspace.Decode(os.Stdin)
	if err != nil {
		fail(err.Error())
	}
	result, err := workspace.Execute("/workspace", req)
	if err != nil {
		fail(err.Error())
	}
	data, err := json.Marshal(result)
	if err != nil || len(data) > workspace.MaxResponseBytes {
		fail("file response exceeds limit")
	}
	os.Stdout.Write(append(data, '\n'))
}

func fail(message string) {
	json.NewEncoder(os.Stdout).Encode(map[string]any{"denyLayer": "workspace-policy", "error": message})
	os.Exit(2)
}
