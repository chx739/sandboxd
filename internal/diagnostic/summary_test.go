package diagnostic

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestSummarizePodList(t *testing.T) {
	raw := `{"items":[{
		"metadata":{"name":"crashloop-demo-abcde","annotations":{"large":"ignored"}},
		"spec":{"containers":[{"image":"ignored"}]},
		"status":{"phase":"Running","containerStatuses":[{"restartCount":2},{"restartCount":1}]}
	}]}`

	summary, err := SummarizePodList(raw)
	if err != nil {
		t.Fatalf("SummarizePodList() error = %v", err)
	}
	if strings.Contains(summary, "annotations") || strings.Contains(summary, "image") {
		t.Fatalf("summary contains non-diagnostic fields: %s", summary)
	}

	var result podListSummary
	if err := json.Unmarshal([]byte(summary), &result); err != nil {
		t.Fatalf("summary is not JSON: %v", err)
	}
	if len(result.Items) != 1 {
		t.Fatalf("items = %d, want 1", len(result.Items))
	}
	if result.Items[0].Metadata.Name != "crashloop-demo-abcde" {
		t.Fatalf("name = %q", result.Items[0].Metadata.Name)
	}
	if result.Items[0].Status.RestartCount != 3 {
		t.Fatalf("restartCount = %d, want 3", result.Items[0].Status.RestartCount)
	}
}

func TestSummarizePodListRejectsMalformedJSON(t *testing.T) {
	if _, err := SummarizePodList(`{"items":[`); err == nil {
		t.Fatal("SummarizePodList() error = nil, want malformed JSON error")
	}
}
