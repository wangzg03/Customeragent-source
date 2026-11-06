<template>
  <section class="knowledge">
    <h2>政策知识库与检索</h2>
    <p>知识条款、评测标签与账户事实分开管理。当前为虚构政策；未审批内容不入索引，检索先过滤场景、生效时间与业务分支。</p>
    <button @click="load">刷新知识版本</button>
    <p v-if="manifest">当前快照：<code>{{ manifest.snapshot_id }}</code><br />来源 {{ manifest.source_records }} 条，已审批索引 {{ manifest.indexed_approved_records }} 条（含历史版本）；{{ manifest.embedding_model }} · {{ manifest.embedding_dimensions }} 维。</p>
    <p v-else>知识库尚未构建。</p>
    <form @submit.prevent="search"><label>业务场景<select v-model="scenario"><option value="delivery">配送</option><option value="course">课程</option></select></label><label v-if="scenario==='delivery'">合同类型<select v-model="contract"><option value="any">通用条款（未指定）</option><option value="single">单日</option><option value="multi">多日</option></select></label><label v-else>发布方式<select v-model="publish"><option value="any">通用条款（未指定）</option><option value="web">Web</option><option value="third_party">第三方</option></select></label><label class="query">检索问题<input v-model="query" maxlength="1000" required /></label><button type="submit" :disabled="busy || !manifest">{{ busy?'正在检索…':'检索条款' }}</button></form>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="result"><p>{{ result.answerable?'找到可用证据':'证据不足，请澄清或转人工' }} · {{ result.latency_ms.toFixed(1) }} ms</p><article v-for="hit in result.hits" :key="hit.document.id"><b>{{ hit.rank }}. {{ hit.document.title }}</b><p>{{ hit.document.body }}</p><small>{{ hit.document.id }} · v{{ hit.document.version }} · {{ hit.document.valid_from }} 至 {{ hit.document.valid_until }}（不含）</small><small>来源：{{ hit.document.source }} · 语义分数 {{ hit.dense_score.toFixed(3) }} · BM25 {{ hit.bm25_score.toFixed(3) }}</small></article><small>花括号是账户字段占位符，由可信业务查询填充。证据不足时仍展示候选供诊断，不能作为回答依据。</small></template>
  </section>
</template>
<script setup lang="ts">
import {onMounted,ref} from 'vue'
const props=defineProps<{apiKey:string}>()
const manifest=ref<any>(null),result=ref<any>(null),error=ref(''),busy=ref(false)
const query=ref(''),scenario=ref('delivery'),contract=ref('any'),publish=ref('any')
async function call(path:string,options:RequestInit={}){const r=await fetch('/api/v2/knowledge'+path,{...options,headers:{'Content-Type':'application/json',...(props.apiKey?{'X-API-Key':props.apiKey}:{}),...options.headers}});const value=await r.json();if(!r.ok)throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));return value}
async function load(){try{manifest.value=(await call('')).manifest;error.value=''}catch(e){error.value=String(e)}}
async function search(){busy.value=true;try{result.value=await call('/search',{method:'POST',body:JSON.stringify({query:query.value,scenario:scenario.value,contract:scenario.value==='delivery'?contract.value:'any',publish:scenario.value==='course'?publish.value:'any',snapshot:manifest.value.snapshot_id})});error.value=''}catch(e){error.value=String(e)}finally{busy.value=false}}
onMounted(load)
</script>
<style scoped>
.knowledge{line-height:1.8;padding:24px 0;overflow-wrap:anywhere}form{display:flex;align-items:end;gap:16px;flex-wrap:wrap;margin:24px 0}label{display:flex;flex-direction:column;gap:6px}.query{flex:1;min-width:180px}input,select,button{font:inherit;padding:10px;border:1px solid #cfddda;border-radius:6px;background:white;color:#19343c}button{cursor:pointer}button:disabled{opacity:.5}article{padding:18px;background:white;border:1px solid #dde8e4;border-radius:8px;margin:14px 0}small{display:block;color:#60797d}code{word-break:break-all}
</style>
