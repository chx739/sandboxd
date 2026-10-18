import json
import unittest

import httpx

from agentd.app.clients import SandboxdClient


class RestoreClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[]
        self.target={"workspaceId":"workspace-"+"1"*32,"sessionId":"session-"+"2"*16,"branchId":"branch-"+"3"*32}
        self.cp="cp-123456789abc4def8123456789abcdef"
        self.source={"snapshotId":"4"*64,"manifestSha256":"5"*64,"repositoryCryptoId":"6"*64,
                     "imageRef":"fixed-image","imageId":"sha256:"+"7"*64}
        def handle(request):
            self.calls.append(request);return httpx.Response(200,json={"barrier":{"phase":"reserved"}})
        self.client=SandboxdClient("http://sandboxd.test","synthetic-token")
        await self.client._client.aclose()
        self.client._client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.addAsyncCleanup(self.client.close)

    async def test_fixed_restore_actions_and_scoped_request(self):
        for action in ("reserve","start","wait","abort","finish","fail"):
            await self.client.workspace_restore(action,self.target,self.cp,"restore-a",self.source,timeout=.25)
            request=self.calls[-1]
            self.assertEqual(request.url.path,"/api/v1/workspaces/"+self.target["workspaceId"]+"/restore/"+action)
            body=json.loads(request.content)
            self.assertEqual(body,{"sessionId":self.target["sessionId"],"branchId":self.target["branchId"],"checkpointId":self.cp,"jobId":"restore-a","source":self.source,"timeoutMs":250})
            self.assertEqual(request.extensions["timeout"]["read"],35 if action in {"abort","finish","fail"} else 2.25)
        await self.client.get_workspace_restore(self.target["workspaceId"])
        self.assertEqual(len(self.calls),7)

    async def test_bad_scope_source_config_and_timeout_never_send(self):
        for action in ("shell","unlock","forget"):
            with self.assertRaises(ValueError):await self.client.workspace_restore(action,self.target,self.cp,"restore-a",self.source)
        for timeout in (True,0,-1,91,float("nan")):
            with self.assertRaises(ValueError):await self.client.workspace_restore("wait",self.target,self.cp,"restore-a",self.source,timeout=timeout)
        for source in ({**self.source,"argv":"bad"},{**self.source,"repositoryCryptoId":"http://other"},{**self.source,"imageId":"mutable-tag"}):
            with self.assertRaises(ValueError):await self.client.workspace_restore("start",self.target,self.cp,"restore-a",source)
        with self.assertRaises(ValueError):await self.client.workspace_restore("start",{**self.target,"image":"bad"},self.cp,"restore-a",self.source)
        with self.assertRaises(ValueError):await self.client.get_workspace_restore("../escape")
        self.assertEqual(self.calls,[])

    async def test_uncertain_start_is_not_retried(self):
        calls=0
        def lost(request):
            nonlocal calls
            calls+=1;raise httpx.ReadTimeout("synthetic acknowledgement loss",request=request)
        await self.client._client.aclose()
        self.client._client=httpx.AsyncClient(transport=httpx.MockTransport(lost))
        with self.assertRaises(httpx.ReadTimeout):await self.client.workspace_restore("start",self.target,self.cp,"restore-a",self.source)
        self.assertEqual(calls,1)
