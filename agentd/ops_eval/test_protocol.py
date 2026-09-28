"""只在隔离 Ragas 环境运行，Mock 模型响应不能当作质量分数。"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from .ragas_adapter import score_ragas


class FakeStructuredChat:
    def __init__(self, verdict=1):
        self.verdict=verdict;self.calls=[]
    async def complete(self, **kwargs):
        self.calls.append(kwargs['tag'])
        schema=json.loads(kwargs['system'].split('schema: ',1)[1])
        name=schema['title']
        if name=='ClaimDecompositionOutput':return json.dumps({'claims':['Synthetic fact.']})
        if name=='StatementGeneratorOutput':return json.dumps({'statements':['Synthetic fact.']})
        if name=='NLIStatementOutput':return json.dumps({'statements':[{'statement':'Synthetic fact.','reason':'Mock protocol only.','verdict':self.verdict}]})
        raise ValueError(name)


class RagasProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_official_metrics_execute_with_structured_fake(self):
        fake=FakeStructuredChat()
        result=await score_ragas(fake,question='What?',answer='Synthetic fact.',reference='Synthetic fact.',contexts=['Synthetic fact.'],tag='protocol')
        self.assertEqual(len(fake.calls),6)
        self.assertEqual(result['faithfulness'],1)
        self.assertEqual(result['factualCorrectnessF1'],1)
        self.assertEqual(len(result['judgeEvidence']),6)
        false=await score_ragas(FakeStructuredChat(0),question='What?',answer='Wrong.',reference='Fact.',contexts=['Fact.'],tag='protocol')
        self.assertEqual(false['faithfulness'],0)
        self.assertEqual(false['factualCorrectnessF1'],0)

    async def test_no_retrieval_faithfulness_is_not_a_fake_zero(self):
        fake=FakeStructuredChat()
        result=await score_ragas(fake,question='What?',answer='Fact.',reference='Fact.',contexts=[],tag='protocol')
        self.assertEqual(len(fake.calls),4)
        self.assertIsNone(result['faithfulness'])

    async def test_generation_pipeline_records_both_modes_without_reference_leak(self):
        from .generation import evaluate
        class FakeGenerationChat:
            calls=0
            accounted=0.0
            stopped=False
            async def complete(inner, **kwargs):
                inner.calls+=1
                if kwargs['tag'].endswith(':generate'):
                    self.assertNotIn('OnlyReference',kwargs['user'])
                    mode=json.loads(kwargs['user'])['mode']
                    return json.dumps({'answer':'Synthetic fact.','citations':['c1'] if mode=='rag' else [],
                        'steps':['Check state.'],'conditions':[],'missing_information':[],'abstained':False})
                if kwargs['tag'].endswith(':rubric'):
                    data=json.loads(kwargs['user']); item={'satisfied':True,'reason':'Mock only.'}
                    return json.dumps({'steps':[item for _ in data['requiredSteps']],
                        'citation_support':[item for _ in data['answer']['citations']],
                        'applicability':item,'avoids_unsupported_conclusions':item,'handles_insufficient_evidence':item})
                return await FakeStructuredChat().complete(**kwargs)
        inputs=[{'case':{'queryId':'test1','query':'What?','split':'dev','answerable':True,
                          'referenceAnswerPoints':['OnlyReference'],'requiredSteps':['Check state.'],'forbiddenConclusions':['Unsafe.']},
                 'evidence':[{'chunkId':'c1','title':'Synthetic','snippet':'Synthetic fact.','source':'synthetic://test'}],
                 'retrievalLatenciesMs':{'rerank':10}}]
        with TemporaryDirectory(dir='/tmp') as directory:
            chat=FakeGenerationChat()
            result=await evaluate(inputs,chat,Path(directory)/'result.json')
            self.assertEqual(result['status'],'completed')
            self.assertEqual(chat.calls,14)
            self.assertEqual(len(result['records']),2)
            self.assertIsNone(result['records'][0]['ragas']['faithfulness'])
            self.assertTrue(result['records'][1]['deterministicChecks']['citationIdsValid'])


if __name__=='__main__':unittest.main()
