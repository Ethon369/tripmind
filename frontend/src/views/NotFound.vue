<template>
  <div class="not-found">
    <a-result status="404" title="404" sub-title="抱歉,这个页面不存在">
      <template #extra>
        <a-space>
          <a-button type="primary" @click="router.push('/')">返回首页</a-button>
          <a-button @click="router.push('/history')">看看历史行程</a-button>
        </a-space>
      </template>
    </a-result>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style scoped>
.not-found {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
}
</style>
