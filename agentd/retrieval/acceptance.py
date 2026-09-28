"""真实 Milvus 小快照增改删/过滤验收；不删除任何已有索引。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from .core import load_corpus
from .snapshot import revise
from .bge_local import LocalBGE
from .milvus_hybrid import MilvusHybrid


def run():
    models=Path.home()/'.local/share/sandboxd/models'
    embedding=models/'multilingual-e5-small-614241f'
    model=LocalBGE(embedding,models/'bge-reranker-base-2cfc18c')
    with TemporaryDirectory(dir='/tmp',prefix='sandboxd-index-acceptance-') as directory:
        root=Path(directory);source=root/'base.jsonl'
        rows=[{'chunkId':f'crud-{x}:s001:p01','docId':f'crud-{x}','title':f'synthetic {x}',
               'text':'Synthetic connection refused diagnosis.' if x=='a' else 'Synthetic memory pressure diagnosis.',
               'source':f'synthetic://crud/{x}','corpusVersion':'ops-lifecycle-v1','component':'fixture','sourceRevision':'v1'} for x in ['a','b']]
        source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        base,digest=load_corpus(source)
        with MilvusHybrid('ops-lifecycle-v1',digest,embedding_id=embedding.name) as original:
            original.rebuild(base,model.encode_passages([c.title+'\n'+c.text for c in base]))
            patch=root/'replacement.jsonl';updated={**rows[0],'text':'Updated synthetic timeout diagnosis.','sourceRevision':'v2'}
            patch.write_text(json.dumps(updated)+'\n')
            revised=root/'revised';revise(source,revised,upsert=patch,delete=['crud-b'])
            chunks,new_digest=load_corpus(revised/'corpus.jsonl')
            with MilvusHybrid('ops-lifecycle-v1',new_digest,embedding_id=embedding.name) as new:
                new.rebuild(chunks,model.encode_passages([c.title+'\n'+c.text for c in chunks]))
                vector=model.encode_query('timeout diagnosis')
                filters={'doc_id':'crud-a','source_revision':'v2','component':'fixture','source':'synthetic://crud/a'}
                dense=new.search(vector,filters=filters)
                sparse=new.bm25.search('timeout',filters=filters)
                assert dense and sparse and {h.chunk_id for h in dense+sparse}=={'crud-a:s001:p01'}
                assert new.search(vector,filters={'doc_id':'crud-b'})==[]
                assert new.bm25.search('timeout',filters={'source_revision':'v1'})==[]
                original.validate(base)
                return {'kind':'real-milvus-local-model-synthetic-lifecycle','baseCollection':original.collection,
                        'newCollection':new.collection,'baseCount':original.count(),'newCount':new.count(),
                        'sourceSnapshotRetained':source.exists(),'oldIndexRetained':original.count()==2,
                        'updateDeleteFilterPassed':True,'externalModelCalls':0}


if __name__=='__main__':print(json.dumps(run(),indent=2))
