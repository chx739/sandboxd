package main

import (
	"context"
	"encoding/json"
	"os"
	"time"

	"github.com/chx739/sandboxd/internal/backuphelper"
)

func main() {
	r, err := backuphelper.Parse(os.Args[1:])
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": false, "error": "invalid fixed maintenance request"})
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	result, err := backuphelper.Run(ctx, r)
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": false, "error": err.Error(), "outcomeUncertain": r.Operation == "backup" || r.Operation == "restore" || r.Operation == "forget" || r.Operation == "prune" || r.Operation == "init-owned"})
		os.Exit(1)
	}
	result["ok"] = true
	json.NewEncoder(os.Stdout).Encode(result)
}
