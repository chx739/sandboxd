import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from agentd.ops_eval.budget import BoundedChat, BudgetExceeded, MODELS
from agentd.ops_eval.generation import load_inputs, plan, ROOT


class OpsEvalTest(unittest.IsolatedAsyncioTestCase):
    async def test_budget_counts_calls_before_network_and_never_logs_key(self):
        calls = []
        async def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}],
                                           'usage':{'prompt_tokens':10,'completion_tokens':5}})
        with TemporaryDirectory(dir='/tmp') as directory:
            ledger = Path(directory)/'calls.jsonl'
            chat = BoundedChat('test-key-not-in-ledger',max_calls=1,max_usd=.1,ledger=ledger,transport=httpx.MockTransport(handler))
            try:
                await chat.complete(model=MODELS['generator'],system='Return JSON',user='synthetic',tag='test')
                with self.assertRaises(BudgetExceeded):
                    await chat.complete(model=MODELS['generator'],system='JSON',user='second',tag='test')
            finally: await chat.close()
            self.assertEqual(len(calls),1)
            self.assertNotIn('test-key-not-in-ledger',ledger.read_text())
            self.assertEqual(json.loads(ledger.read_text().splitlines()[0])['status'],'attempt')

    async def test_failure_latches_stop_and_retains_reservation(self):
        attempts = []
        async def handler(request):
            attempts.append(1);return httpx.Response(429,json={'error':'synthetic'})
        with TemporaryDirectory(dir='/tmp') as directory:
            chat = BoundedChat('fake',max_calls=10,max_usd=1,ledger=Path(directory)/'calls.jsonl',transport=httpx.MockTransport(handler))
            try:
                with self.assertRaises(httpx.HTTPStatusError):
                    await chat.complete(model=MODELS['judge'],system='JSON',user='test',tag='one')
                with self.assertRaises(BudgetExceeded):
                    await chat.complete(model=MODELS['judge'],system='JSON',user='test',tag='two')
                self.assertGreater(chat.accounted,0)
                self.assertEqual(len(attempts),1)
            finally: await chat.close()

    def test_generation_plan_fixed_inputs_and_hash(self):
        path=ROOT/'docs/evidence/phase23-ops-rag-results.json'
        inputs=load_inputs(path,['ops01','ops53'])
        p=plan(inputs)
        self.assertEqual(p['maximumCalls'],32)
        self.assertEqual(p,plan(inputs))
        self.assertTrue(inputs[1]['case']['answerable'] is False)

    async def test_live_action_adapter_uses_registered_tools_only(self):
        from agentd.ops_eval.live_agent import LiveActionGateway
        from langchain_core.messages import HumanMessage
        class Fake:
            async def complete(self, **kwargs):
                return json.dumps({'action':'tool','tool':'search_logs','arguments':{'service':'payments'}})
        session = LiveActionGateway(Fake(), 'test').new_session([{'type':'function','function':{'name':'search_logs'}}])
        result = await session.invoke([HumanMessage(content='synthetic')])
        self.assertEqual(result.message.tool_calls[0]['name'], 'search_logs')
        forbidden = LiveActionGateway(Fake(), 'test').new_session([{'type':'function','function':{'name':'search_knowledge'}}])
        with self.assertRaises(ValueError):
            await forbidden.invoke([HumanMessage(content='synthetic')])
