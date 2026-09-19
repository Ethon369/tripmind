<template>
  <div class="users">
    <header class="hd">
      <div class="hd-text">
        <h1 class="hd-title">账号管理</h1>
        <p class="hd-sub">
          管理员账号可以管理账号、使用知识库、查看所有人的历史行程；普通账号只看得到自己的行程
        </p>
      </div>
      <div class="hd-actions">
        <a-button :loading="loading" @click="load">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
        <a-button type="primary" @click="openCreate">
          <template #icon><PlusOutlined /></template>
          新建账号
        </a-button>
      </div>
    </header>

    <a-row v-if="summary && summary.total" :gutter="[16, 16]" class="stats">
      <a-col :xs="12" :sm="6">
        <div class="stat">
          <span class="stat-label">账号总数</span>
          <span class="stat-value u-num">{{ summary.total }}<em>个</em></span>
        </div>
      </a-col>
      <a-col :xs="12" :sm="6">
        <div class="stat">
          <span class="stat-label">管理员</span>
          <span class="stat-value u-num">{{ summary.admins }}<em>个</em></span>
        </div>
      </a-col>
      <a-col :xs="12" :sm="6">
        <div class="stat">
          <span class="stat-label">启用中</span>
          <span class="stat-value u-num">{{ summary.active }}<em>个</em></span>
        </div>
      </a-col>
      <a-col :xs="12" :sm="6">
        <div class="stat">
          <span class="stat-label">已停用</span>
          <span class="stat-value u-num">{{ summary.disabled }}<em>个</em></span>
        </div>
      </a-col>
    </a-row>

    <StatePanel
      v-if="error"
      state="error"
      error-kind="network"
      title="读取账号列表失败"
      :detail="error"
      @retry="load"
    />

    <StatePanel v-else-if="loading" state="loading" skeleton="table" :skeleton-count="5" />

    <a-card v-else :bordered="false" class="table-card">
      <a-table
        :data-source="users"
        :columns="columns"
        row-key="id"
        :pagination="false"
        :scroll="{ x: 'max-content' }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'username'">
            <div class="uname">
              <span class="uname-text">{{ record.username }}</span>
              <!-- 显示名和用户名不同时补一行 —— 账号管理页要能回答
                   "这个用户名背后是谁"。 -->
              <span v-if="record.display_name && record.display_name !== record.username" class="uname-sub">
                {{ record.display_name }}
              </span>
            </div>
            <div v-if="record.id === currentUserId" class="uname-self">当前登录</div>
          </template>

          <template v-else-if="column.key === 'role'">
            <a-tag :color="record.role === 'admin' ? 'purple' : 'default'">
              {{ record.role === 'admin' ? '管理员' : '普通用户' }}
            </a-tag>
          </template>

          <template v-else-if="column.key === 'status'">
            <a-badge
              :status="record.is_active ? 'success' : 'default'"
              :text="record.is_active ? '启用' : '已停用'"
            />
          </template>

          <template v-else-if="column.key === 'plans'">
            <span class="u-num">{{ record.plan_count ?? 0 }}</span>
          </template>

          <template v-else-if="column.key === 'last_login'">
            <span class="muted">{{ record.last_login_at ? fmtDate(record.last_login_at) : '从未登录' }}</span>
          </template>

          <template v-else-if="column.key === 'action'">
            <a-space :size="4">
              <a-button type="link" size="small" @click="openEdit(record)">编辑</a-button>
              <a-dropdown :trigger="['click']">
                <a-button type="link" size="small">
                  更多 <DownOutlined />
                </a-button>
                <template #overlay>
                  <a-menu @click="onMenuClick($event, record)">
                    <a-menu-item key="passwd">重置密码</a-menu-item>
                    <a-menu-item key="revoke" :disabled="record.id === currentUserId">
                      强制下线
                    </a-menu-item>
                    <a-menu-divider />
                    <a-menu-item
                      key="toggle"
                      :disabled="record.id === currentUserId"
                    >
                      {{ record.is_active ? '停用账号' : '启用账号' }}
                    </a-menu-item>
                    <a-menu-item key="delete" danger :disabled="record.id === currentUserId">
                      删除账号
                    </a-menu-item>
                  </a-menu>
                </template>
              </a-dropdown>
            </a-space>
          </template>
        </template>
      </a-table>

      <p class="note">
        删除账号<strong>不会</strong>删除 TA 名下的行程，那些行程会变成「无主」——
        只有管理员在「历史行程」里把范围切到“全部用户”时才看得到。
        这是为了让累计成本统计不会随着删账号而缩水。
      </p>
    </a-card>

    <!-- ---------------- 新建 ---------------- -->
    <a-modal
      v-model:open="createOpen"
      title="新建账号"
      :confirm-loading="saving"
      :ok-text="'创建'"
      :width="460"
      :centered="true"
      @ok="submitCreate"
    >
      <a-form layout="vertical" :model="createForm">
        <a-form-item label="用户名" required>
          <a-input
            v-model:value="createForm.username"
            placeholder="3-32 位，字母数字开头，可含 _ . -"
            autocomplete="off"
          />
        </a-form-item>
        <a-form-item label="密码" required>
          <a-input-password
            v-model:value="createForm.password"
            placeholder="至少 6 位；不能与用户名相同"
            autocomplete="new-password"
          />
        </a-form-item>
        <a-form-item label="角色">
          <a-radio-group v-model:value="createForm.role">
            <a-radio value="user">普通用户</a-radio>
            <a-radio value="admin">管理员</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="显示名（可选）">
          <a-input v-model:value="createForm.display_name" placeholder="留空则显示用户名" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- ---------------- 编辑 ---------------- -->
    <a-modal
      v-model:open="editOpen"
      :title="`编辑账号「${editing?.username ?? ''}」`"
      :confirm-loading="saving"
      :width="460"
      :centered="true"
      @ok="submitEdit"
    >
      <a-form layout="vertical">
        <a-form-item label="角色">
          <a-select v-model:value="editForm.role" :disabled="isSelf">
            <a-select-option value="user">普通用户</a-select-option>
            <a-select-option value="admin">管理员</a-select-option>
          </a-select>
          <p v-if="isSelf" class="hint">不能修改自己的角色 —— 请用另一个管理员账号操作</p>
        </a-form-item>
        <a-form-item label="状态">
          <a-switch
            v-model:checked="editForm.is_active"
            :disabled="isSelf"
            checked-children="启用"
            un-checked-children="停用"
          />
          <p v-if="isSelf" class="hint">不能停用当前登录的账号</p>
        </a-form-item>
        <a-form-item label="显示名">
          <!-- 清空显示名是**合法操作**，所以这里不能把空串当"没填"处理：
               后端用 model_fields_set 区分，前端就如实送空串。 -->
          <a-input v-model:value="editForm.display_name" placeholder="留空则显示用户名" allow-clear />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- ---------------- 重置密码 ---------------- -->
    <a-modal
      v-model:open="passwdOpen"
      :title="`重置「${editing?.username ?? ''}」的密码`"
      :confirm-loading="saving"
      :width="440"
      :centered="true"
      ok-text="重置"
      @ok="submitPasswd"
    >
      <a-form layout="vertical">
        <a-form-item label="新密码" required>
          <a-input-password
            v-model:value="passwdForm.password"
            placeholder="至少 6 位；不能与用户名相同"
            autocomplete="new-password"
          />
        </a-form-item>
        <a-alert
          type="warning"
          show-icon
          message="重置后该账号的所有登录会立即失效"
          description="包括 TA 现在开着的页面 —— 下一个请求就会被要求重新登录。"
        />
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { Modal, message } from 'ant-design-vue';
import { ReloadOutlined, PlusOutlined, DownOutlined } from '@ant-design/icons-vue';
import StatePanel from '@/components/common/StatePanel.vue';
import { useAuth } from '@/composables/useAuth';
import {
  createUser,
  deleteUser,
  listUsers,
  revokeUserSessions,
  updateUser
} from '@/services/api';
import type { AuthUser, UserListResponse, UserRole } from '@/types';

/**
 * 账号管理（仅管理员可达）。
 *
 * 页面上有**三处"不能对自己做"**的禁用：改角色、停用、删除、强制下线。
 * 这些不是纯前端的客气 —— 后端也有对应的守卫（不能删最后一个管理员），
 * 前端的禁用只是让"点了会失败"变成"根本点不了"，少一次无意义的报错。
 *
 * 唯一**没有**在前端拦的是"把自己降级/停用"以外的自伤操作:
 * `isSelf` 判据用 id 而不是用户名 —— 用户名可以改，id 不会。
 */

const { user: currentUser } = useAuth();

const currentUserId = computed(() => currentUser.value?.id ?? '');

const users = ref<AuthUser[]>([]);
const summary = ref<UserListResponse['summary'] | null>(null);
const loading = ref(false);
const error = ref('');
const saving = ref(false);

const columns = [
  { title: '账号', key: 'username', width: 200 },
  { title: '角色', key: 'role', width: 110 },
  { title: '状态', key: 'status', width: 110 },
  { title: '行程数', key: 'plans', width: 90 },
  { title: '最近登录', key: 'last_login', width: 170 },
  { title: '操作', key: 'action', width: 140 }
];

const fmtDate = (s: string) => (s || '').replace('T', ' ').slice(0, 16) || '—';

// ---------------- 加载 ----------------

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const res = await listUsers();
    users.value = res.users || [];
    summary.value = res.summary || null;
  } catch (e: unknown) {
    error.value = (e as Error)?.message || '读取账号列表失败';
    users.value = [];
    summary.value = null;
  } finally {
    loading.value = false;
  }
}

// ---------------- 新建 ----------------

const createOpen = ref(false);
const createForm = reactive({
  username: '',
  password: '',
  role: 'user' as UserRole,
  display_name: ''
});

function openCreate() {
  createForm.username = '';
  createForm.password = '';
  createForm.role = 'user';
  createForm.display_name = '';
  createOpen.value = true;
}

async function submitCreate() {
  if (!createForm.username.trim() || !createForm.password) {
    message.warning('用户名和密码都要填');
    return;
  }
  saving.value = true;
  try {
    await createUser({
      username: createForm.username.trim(),
      password: createForm.password,
      role: createForm.role,
      display_name: createForm.display_name.trim() || undefined
    });
    message.success('账号已创建');
    createOpen.value = false;
    await load();
  } catch (e: unknown) {
    message.error((e as Error)?.message || '创建失败');
  } finally {
    saving.value = false;
  }
}

// ---------------- 编辑 ----------------

const editOpen = ref(false);
const editing = ref<AuthUser | null>(null);
const editForm = reactive({ role: 'user' as UserRole, is_active: true, display_name: '' });

const isSelf = computed(() => !!editing.value && editing.value.id === currentUserId.value);

function openEdit(u: AuthUser) {
  editing.value = u;
  editForm.role = u.role;
  editForm.is_active = u.is_active;
  editForm.display_name = u.display_name || '';
  editOpen.value = true;
}

async function submitEdit() {
  if (!editing.value) return;
  saving.value = true;
  try {
    // 只送**实际被改过**的字段：显示名允许被清空，所以它单独判
    // "和原值不同"，而不是判"非空"。
    const payload: {
      role?: UserRole
      is_active?: boolean
      display_name?: string | null
    } = {};
    if (!isSelf.value && editForm.role !== editing.value.role) payload.role = editForm.role;
    if (!isSelf.value && editForm.is_active !== editing.value.is_active) {
      payload.is_active = editForm.is_active;
    }
    const nextName = editForm.display_name.trim();
    if (nextName !== (editing.value.display_name || '')) {
      payload.display_name = nextName; // 空串 = 清空
    }

    if (Object.keys(payload).length === 0) {
      editOpen.value = false;
      return;
    }

    await updateUser(editing.value.id, payload);
    message.success('已保存');
    editOpen.value = false;
    await load();
  } catch (e: unknown) {
    message.error((e as Error)?.message || '保存失败');
  } finally {
    saving.value = false;
  }
}

// ---------------- 重置密码 ----------------

const passwdOpen = ref(false);
const passwdForm = reactive({ password: '' });

function openPasswd(u: AuthUser) {
  editing.value = u;
  passwdForm.password = '';
  passwdOpen.value = true;
}

async function submitPasswd() {
  if (!editing.value) return;
  if (!passwdForm.password) {
    message.warning('请输入新密码');
    return;
  }
  saving.value = true;
  try {
    await updateUser(editing.value.id, { password: passwdForm.password });
    message.success('密码已重置，该账号的其它登录已失效');
    passwdOpen.value = false;
    await load();
  } catch (e: unknown) {
    message.error((e as Error)?.message || '重置失败');
  } finally {
    saving.value = false;
  }
}

// ---------------- 行内菜单 ----------------

async function toggleActive(u: AuthUser) {
  try {
    await updateUser(u.id, { is_active: !u.is_active });
    message.success(u.is_active ? '账号已停用' : '账号已启用');
    await load();
  } catch (e: unknown) {
    message.error((e as Error)?.message || '操作失败');
  }
}

async function revoke(u: AuthUser) {
  try {
    const msg = await revokeUserSessions(u.id);
    message.success(msg || '已强制下线');
  } catch (e: unknown) {
    message.error((e as Error)?.message || '操作失败');
  }
}

function confirmDelete(u: AuthUser) {
  Modal.confirm({
    title: `删除账号「${u.username}」？`,
    content:
      '账号会立即失效，TA 名下的行程不会被删除，只会变成「无主」——' +
      '仅管理员可见。这个操作无法撤销。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await deleteUser(u.id);
        message.success('账号已删除');
        await load();
      } catch (e: unknown) {
        message.error((e as Error)?.message || '删除失败');
      }
    }
  });
}

function onMenu(key: string, u: AuthUser) {
  if (key === 'passwd') return openPasswd(u);
  if (key === 'revoke') return void revoke(u);
  if (key === 'toggle') return void toggleActive(u);
  if (key === 'delete') return confirmDelete(u);
}

/**
 * antd 的 `a-menu` 点击回调。
 *
 * 单独包一层而不是在模板里写箭头函数：模板里的形参类型注解要依赖
 * SFC 编译器的 TS 支持，写起来脆（改一次 vue 版本就可能报"表达式里
 * 不支持类型注解"）。这里把入参收成 `unknown` 再手动收窄，模板侧
 * 就只剩一个普通函数调用。
 */
function onMenuClick(e: unknown, record: AuthUser) {
  const key =
    typeof e === 'object' && e !== null ? String((e as { key?: unknown }).key ?? '') : '';
  if (key) onMenu(key, record);
}

onMounted(load);
</script>

<style scoped>
.users {
  max-width: var(--container-max);
  margin: 0 auto;
  padding: var(--sp-8) var(--sp-4) var(--sp-10);
}

/* ---------------- 页头 ---------------- */
.hd {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
  flex-wrap: wrap;
  margin-bottom: var(--sp-6);
}

.hd-title {
  margin-bottom: var(--sp-1);
}

.hd-sub {
  color: var(--text-2);
  font-size: var(--fs-sm);
  max-width: 62ch;
}

.hd-actions {
  display: flex;
  gap: var(--sp-3);
  flex-wrap: wrap;
}

/* ---------------- 统计 ---------------- */
.stats {
  margin-bottom: var(--sp-5);
}

.stat {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  background: var(--bg-card);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
  padding: var(--sp-4);
  height: 100%;
}

.stat-label {
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.stat-value {
  font-size: var(--fs-h2);
  font-weight: var(--fw-semibold);
  color: var(--text-1);
}

.stat-value em {
  font-style: normal;
  font-size: var(--fs-caption);
  font-weight: var(--fw-regular);
  color: var(--text-2);
  margin-left: 2px;
}

/* ---------------- 表格 ---------------- */
.table-card :deep(.ant-card-body) {
  padding: var(--sp-2) var(--sp-4) var(--sp-4);
}

.uname-text {
  font-weight: var(--fw-semibold);
}

.uname-sub {
  display: block;
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.uname-self {
  font-size: var(--fs-caption);
  color: var(--brand-600);
}

.muted {
  color: var(--text-2);
}

.note {
  margin: var(--sp-4) 0 0;
  padding-top: var(--sp-3);
  border-top: 1px solid var(--line-1);
  font-size: var(--fs-caption);
  line-height: var(--lh-body);
  color: var(--text-2);
}

.hint {
  margin: var(--sp-1) 0 0;
  font-size: var(--fs-caption);
  color: var(--text-3);
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 767px) {
  .users {
    padding: var(--sp-4) var(--sp-3) var(--sp-8);
  }

  .hd-actions {
    width: 100%;
  }

  .hd-actions :deep(.ant-btn) {
    flex: 1 1 0;
    min-height: var(--touch-min);
  }
}
</style>
