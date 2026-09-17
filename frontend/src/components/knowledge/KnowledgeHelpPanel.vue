<template>
  <section class="hp">
    <a-collapse v-model:activeKey="activeKeys" :bordered="false" class="hp-collapse">
      <!-- ---------------- 两层知识库 ---------------- -->
      <a-collapse-panel key="layers" header="两层知识库各回答什么问题">
        <table class="hp-table">
          <thead>
            <tr>
              <th>知识层</th>
              <th>数据源</th>
              <th>回答什么</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><code>poi_facts</code></td>
              <td>冻结的高德 POI 库存（1750 条，带真实坐标）</td>
              <td>这城市有哪些<b>真实</b>景点、在哪</td>
            </tr>
            <tr>
              <td><code>city_guides</code></td>
              <td>手写 markdown 攻略（北京 / 上海 / 成都 / 西安 / 杭州）</td>
              <td>怎么安排才合理（门票、预约、淡旺季、避坑）</td>
            </tr>
          </tbody>
        </table>
        <p class="hp-note">
          第二层才是「为什么需要 RAG」的正当答案 —— 门票涨价、闭馆日、预约规则这类信息，
          模型最容易记错或过时。第一层补的是另一个缺口：高德的文字检索只返回名字和地址、
          <b>不返回坐标</b>，模型手上没有坐标就容易编。
        </p>
      </a-collapse-panel>

      <!-- ---------------- 维度 ---------------- -->
      <a-collapse-panel key="dim" header="为什么维度必须是 1024">
        <p class="hp-note">
          collection 名字里带着维度（<code>*_1024</code>）。换 embedding 模型时如果没重建库，
          <b>Milvus 不会报错</b>，而是写入和检索结果全乱 —— 属于静默失败，
          从现象上看只会觉得「检索效果突然变差」。
        </p>
        <p class="hp-note">
          所以自检脚本会把「维度是不是 1024」当成一项硬指标。灌库前如果上方状态显示
          embedding 或 Milvus 不可用，先修好再灌。
        </p>
      </a-collapse-panel>

      <!-- ---------------- 分数 ---------------- -->
      <a-collapse-panel key="score" header="检索分数的含义与边界">
        <ul class="hp-list">
          <li>分数是 <b>COSINE 相似度</b>，值域 [-1, 1]，实际命中通常落在 0.5–0.9。</li>
          <li>
            两层用<b>同一个</b> embedding 模型（<code>BAAI/bge-m3</code>）和同一种度量，
            所以两层分数<b>可以直接比较</b>，归并排序才有意义。
          </li>
          <li>
            反过来说：<b>换成别的 embedding 模型后，这里的分数与别处的分数不可比</b>。
            页面上刻意没有做「大于 0.7 就是好结果」这类颜色分级 —— 那个阈值没有依据，
            用颜色暗示只会误导。
          </li>
        </ul>
      </a-collapse-panel>

      <!-- ---------------- ENABLE_RAG ---------------- -->
      <a-collapse-panel key="enable" header="ENABLE_RAG 开关与评测的关系">
        <p class="hp-note">
          这个开关默认关闭，是一个<b>有意的设计</b>：只有当关闭时 planner 的 prompt
          与「引入 RAG 之前」逐字相同时，「开启 RAG」和「关闭 RAG」两组评测数字才可以直接对比。
          如果关掉开关后 prompt 里还留着一个空的知识库段落，两组的差异就说不清是 RAG 带来的
          还是段落结构带来的。
        </p>
        <p class="hp-note">
          日常使用时把它打开即可。修改方式是在 <code>backend/.env</code> 里写
          <code>ENABLE_RAG=true</code>，然后重启后端 —— 环境变量在进程启动时读取，
          所以没有做在线切换按钮（改了不重启不生效，做个开关反而是欺骗）。
        </p>
      </a-collapse-panel>
    </a-collapse>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue';

/** 默认全部收起：这是「需要时才看」的背景知识，不是主流程 */
const activeKeys = ref<string[]>([]);
</script>

<style scoped>
.hp-collapse {
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
}

.hp-collapse :deep(.ant-collapse-header) {
  font-weight: var(--fw-medium);
  color: var(--text-1);
}

.hp-note {
  font-size: var(--fs-sm);
  color: var(--text-2);
  line-height: var(--lh-body);
  margin-bottom: var(--sp-3);
}

.hp-note:last-child {
  margin-bottom: 0;
}

.hp-list {
  margin: 0;
  padding-left: 1.2em;
  font-size: var(--fs-sm);
  color: var(--text-2);
  line-height: var(--lh-body);
}

.hp-list li {
  margin-bottom: var(--sp-2);
}

.hp-list li:last-child {
  margin-bottom: 0;
}

.hp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
  margin-bottom: var(--sp-3);
}

.hp-table th,
.hp-table td {
  text-align: left;
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--border-2);
  vertical-align: top;
}

.hp-table th {
  color: var(--text-2);
  font-weight: var(--fw-medium);
  background: var(--bg-sunken);
}

.hp-table td {
  color: var(--text-1);
}

code {
  font-family: var(--font-num);
  font-size: 0.95em;
  background: var(--bg-sunken);
  padding: 1px 5px;
  border-radius: 4px;
  border: 1px solid var(--border-2);
}

/* 手机上表格改为纵向堆叠：三列在 375px 里会把文字挤成竖条 */
@media (max-width: 575px) {
  .hp-table,
  .hp-table tbody,
  .hp-table tr,
  .hp-table td {
    display: block;
    width: 100%;
  }

  .hp-table thead {
    display: none;
  }

  .hp-table tr {
    padding: var(--sp-3) 0;
    border-bottom: 1px solid var(--border-2);
  }

  .hp-table td {
    border-bottom: none;
    padding: 2px 0;
  }

  .hp-table td:first-child code {
    font-weight: var(--fw-semibold);
  }
}
</style>
