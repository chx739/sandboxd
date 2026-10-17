package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/chx739/sandboxd/internal/sandbox"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	corelisters "k8s.io/client-go/listers/core/v1"
	"k8s.io/client-go/tools/cache"
)

func TestPersistentWorkspaceCannotUseGenericExec(t *testing.T) {
	for _, bindingLabels := range []map[string]string{
		{sandbox.LabelWorkspace: "workspace-0123456789abcdef0123456789abcdef"},
		{sandbox.LabelSource: "workspace"},
	} {
		indexer := cache.NewIndexer(cache.MetaNamespaceKeyFunc, cache.Indexers{cache.NamespaceIndex: cache.MetaNamespaceIndexFunc})
		bindingLabels[sandbox.LabelManagedBy] = sandbox.ValueManagedBy
		bindingLabels[sandbox.LabelID] = "test"
		indexer.Add(&corev1.Pod{ObjectMeta: metav1.ObjectMeta{Name: "sandbox-test", Namespace: "sandboxd-demo", Labels: bindingLabels}})
		manager := sandbox.NewManager(nil, nil, sandbox.Config{Namespace: "sandboxd-demo"}, corelisters.NewPodLister(indexer))
		server := NewServer(manager, nil, nil, "sandboxd-target", "agent-token", "operator-token", time.Second, time.Second, func() bool { return true })
		request := httptest.NewRequest(http.MethodPost, "/api/v1/sandboxes/test/exec", strings.NewReader(`{"cmd":["sh","-c","touch /workspace/bypass"]}`))
		request.Header.Set("Authorization", "Bearer agent-token")
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		if response.Code != http.StatusForbidden || !strings.Contains(response.Body.String(), "workspace-policy") {
			t.Fatal(response.Code, response.Body.String())
		}
	}
}
