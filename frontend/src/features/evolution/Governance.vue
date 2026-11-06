<template>
  <section class="governance">
    <h2>Harness 与受控自进化</h2>
    <p>固定执行模型 qwen3.5:9b · 规则验收为主 · 当前未启用 LLM 评审</p>
    <p>训练坏例 → 审核过的有限提示补丁 → 六个开发用例成对回放 → 无逐例退化且成功数增加 → 离线版本晋级 → 独立测试。候选不能修改安全守卫、工具权限或评分器。</p>
    <div v-if="error" role="alert" class="failure">{{ error }}</div>
    <div class="cards"><article><small>当前离线策略</small><strong>{{ state?.registry.active ?? '加载中' }}</strong><span>每个任务在提交时冻结版本</span></article><article><small>自进化状态</small><strong>{{ state?.cycle.status ?? '—' }}</strong><span>{{ state?.cycle.phase ?? state?.cycle.result?.status ?? '尚无运行' }}</span></article><article><small>生产发布</small><strong>独立门禁</strong><span>离线晋级不等于真实业务上线</span></article></div>
    <div class="actions"><button @click="load">刷新状态</button><button @click="mutate('/cycles')" :disabled="busy || running">运行一次受控迭代</button><button v-if="running" @click="mutate('/cancel')" :disabled="busy">取消迭代</button><button @click="mutate('/rollback')" :disabled="busy || running || state?.registry.active==='baseline'">回滚到父版本</button></div>
    <p class="note">一次迭代最多 4 个训练用例 + 6 个开发用例各运行两次。固定 Docker 隔离，Token、轮次、上下文和时长预算生效。CPU 推理可能需要数分钟；运行期间暂停接收普通模型任务。</p>
    <details><summary>当前策略补丁与训练证据</summary><pre>{{ JSON.stringify(state?.active_profile,null,2) }}</pre></details>
    <h3>版本决策记录</h3>
    <p v-if="!state?.registry.history.length">尚无决策记录。无增益候选会记录拒绝原因，不会自动晋级。</p>
    <ol><li v-for="(event,i) in state?.registry.history" :key="i"><b>{{ decisionLabel(event.kind) }} · {{ event.candidate ?? event.target }}</b><p v-if="event.gate">成功数变化 {{ event.gate.success_gain_cases }} / {{ event.gate.pairs }} 对；{{ event.gate.passed ? '离线回归门禁通过' : event.gate.reasons.map(reasonLabel).join(' / ') }}</p><details><summary>完整决策证据</summary><pre>{{ JSON.stringify(event,null,2) }}</pre></details></li></ol>
    <h3>执行链路</h3><p>任务入队 → 冻结策略与请求 → pre-step 预算检查 → 模型输出校验 → tool.intent 持久化 → 沙盒执行 → tool.completed 持久化 → 用户观察 → 进展检测 → 规则验收 → 日志重放校验。</p>
    <p>每条轨迹中的“会话事件与重放”可查看事件顺序、哈希和预算。进程重启会将执行中任务标记 interrupted；未完成工具意图不会自动重试。真实外部工具接入仍须实现事务幂等。</p>
  </section>
</template>
<script setup lang="ts">
import {computed,onMounted,onBeforeUnmount,ref} from 'vue'
const props=defineProps<{apiKey:string}>()
const state=ref<any>(null),error=ref(''),busy=ref(false)
const running=computed(()=>state.value?.cycle.status==='running')
function decisionLabel(kind:string){return ({promote:'晋级',reject:'拒绝晋级',rollback:'回滚'} as Record<string,string>)[kind]||kind}
function reasonLabel(reason:string){return ({candidate_safety_or_execution_failure:'候选存在安全违规或执行失败',no_objective_success_gain:'成功数没有客观增益',per_case_success_regression:'原先成功的用例出现退化',candidate_replay_incomplete:'候选日志重放不完整',active_parent_changed:'当前父版本已变化',pair_configuration_mismatch:'成对实验配置不一致',only_dev_can_admit_candidate:'只有独立开发集可用于晋级'} as Record<string,string>)[reason]||reason}
let timer:ReturnType<typeof setInterval>
async function call(path='',method='GET'){
  const r=await fetch('/api/v2/evolution'+path,{method,headers:props.apiKey?{'X-API-Key':props.apiKey}:{}})
  const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data));return data
}
async function load(){try{state.value=await call();error.value=''}catch(e){error.value=String(e)}}
async function mutate(path:string){busy.value=true;try{await call(path,'POST');await load()}catch(e){error.value=String(e)}finally{busy.value=false}}
onMounted(()=>{load();timer=setInterval(load,5000)})
onBeforeUnmount(()=>clearInterval(timer))
</script>
<style scoped>
.governance{padding:24px 0;line-height:1.8}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px;margin:22px 0}.cards article{padding:20px;background:white;border:1px solid #dbe6e2;border-radius:10px;overflow-wrap:anywhere}.cards small,.cards strong,.cards span{display:block}.cards strong{font-size:24px;margin:8px 0}.cards span,.note{font-size:13px;color:#60797d}.actions{display:flex;gap:12px;flex-wrap:wrap}button{border:1px solid #cfddda;border-radius:7px;background:white;color:#19343c;padding:10px 14px;cursor:pointer}button:disabled{opacity:.5;cursor:default}details{margin:18px 0}summary{cursor:pointer}pre{max-height:400px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#eaf1ee;padding:16px;font-size:12px}.failure{background:#fff0ec;color:#a73520;padding:12px}li{margin:20px 0}
</style>
