# Feature: Smart Export - 智能导出功能

## 概述

本功能扩展 DB Query Tool 的导出能力，实现"执行并导出"一键操作，以及通过自然语言智能识别用户的执行和导出意图。

---

## 功能一：Manual SQL 执行并导出

### 需求描述

在 MANUAL SQL 模式下，用户可以一键完成"执行查询 + 导出结果"的操作，无需分两步进行。

### 功能特性

#### 1.1 执行并导出按钮

| 元素 | 说明 |
|------|------|
| 按钮位置 | EXECUTE 按钮右侧，新增下拉按钮组 |
| 按钮文案 | `EXECUTE & EXPORT ▼` |
| 下拉选项 | `Export as CSV` / `Export as JSON` |

#### 1.2 快捷键支持

| 快捷键 | 功能 |
|--------|------|
| `Cmd/Ctrl + Enter` | 执行查询（保持现有功能） |
| `Cmd/Ctrl + Shift + E` | 执行并导出（弹出格式选择） |
| `Cmd/Ctrl + Shift + C` | 执行并导出为 CSV（直接导出） |
| `Cmd/Ctrl + Shift + J` | 执行并导出为 JSON（直接导出） |

#### 1.3 交互流程

```
用户输入 SQL
    ↓
按下 Cmd+Shift+E 或点击 "EXECUTE & EXPORT"
    ↓
┌─────────────────────────────────┐
│  选择导出格式                    │
│  ┌─────────┐  ┌─────────┐       │
│  │   CSV   │  │  JSON   │       │
│  └─────────┘  └─────────┘       │
│              [取消]              │
└─────────────────────────────────┘
    ↓
执行查询 → 显示结果 → 自动下载文件
    ↓
显示成功提示: "查询完成，已导出 N 行到 {filename}"
```

#### 1.4 异常处理

| 场景 | 处理方式 |
|------|----------|
| SQL 为空 | 提示 "Please enter a SQL query" |
| SQL 语法错误 | 显示后端返回的错误信息，不导出 |
| 查询结果为空 | 提示 "Query executed successfully but returned no data" |
| 查询超时 | 提示 "Query timeout, please try a simpler query" |
| 网络错误 | 提示 "Network error, please check your connection" |

#### 1.5 大数据集处理

- 结果超过 10,000 行时显示警告弹窗
- 用户确认后继续导出
- 导出过程中显示 loading 状态

---

## 功能二：自然语言智能识别（AI 驱动）

### 需求描述

在 NATURAL LANGUAGE 模式下，**由 AI 自动识别用户的意图**：
- 仅生成 SQL
- 生成并执行
- 生成、执行并导出

### 功能特性

#### 2.1 AI 意图识别

后端 AI 服务在生成 SQL 的同时，解析用户的完整意图并返回结构化数据。

**API 响应结构变更：**

```typescript
// 原响应
interface NaturalQueryResponse {
  sql: string;
  explanation: string;
}

// 新响应（增加 intent 字段）
interface NaturalQueryResponse {
  sql: string;
  explanation: string;
  intent: {
    execute: boolean;      // 是否需要执行
    export: boolean;       // 是否需要导出
    exportFormat: 'csv' | 'json' | null;  // 导出格式
  };
}
```

#### 2.2 AI Prompt 设计

后端 AI 需要同时完成两个任务：
1. 将自然语言转换为 SQL
2. 识别用户的操作意图

**System Prompt 示例：**

```
You are an expert SQL query generator and user intent analyzer.

Your tasks:
1. Generate a valid SELECT SQL query based on the user's request
2. Analyze the user's intent regarding query execution and export

Intent Analysis Rules:
- execute: true if user wants to see results (keywords: 执行, 运行, 查一下, 看看, show, run, execute, get, fetch)
- export: true if user wants to download/export data (keywords: 导出, 下载, 保存, export, download, save, to file)
- exportFormat: "csv" or "json" based on user preference, default to "csv" if export is true but format not specified

Output JSON format:
{
  "sql": "SELECT ...",
  "explanation": "Brief description of what the query does",
  "intent": {
    "execute": true/false,
    "export": true/false,
    "exportFormat": "csv" | "json" | null
  }
}
```

#### 2.3 意图识别示例

| 用户输入 | AI 识别意图 | 系统行为 |
|----------|-------------|----------|
| "查询所有用户" | `{execute: false, export: false}` | 仅生成 SQL |
| "帮我看看所有部门" | `{execute: true, export: false}` | 生成 + 执行 |
| "执行查询：统计各部门人数" | `{execute: true, export: false}` | 生成 + 执行 |
| "导出所有候选人信息" | `{execute: true, export: true, format: "csv"}` | 生成 + 执行 + 导出 CSV |
| "下载面试记录为JSON" | `{execute: true, export: true, format: "json"}` | 生成 + 执行 + 导出 JSON |
| "把薪资最高的10个职位保存成表格" | `{execute: true, export: true, format: "csv"}` | 生成 + 执行 + 导出 CSV |

#### 2.4 按钮文案调整

| 原文案 | 新文案 | 说明 |
|--------|--------|------|
| `GENERATE SQL` | `SMART QUERY` | 更准确描述智能查询功能 |

#### 2.5 交互流程

```
用户输入: "导出所有来自阿里巴巴的候选人为CSV"
    ↓
点击 "SMART QUERY" 或 Cmd+Enter
    ↓
显示 Loading: "Processing your request..."
    ↓
后端 AI 返回:
{
  "sql": "SELECT * FROM candidates WHERE current_company = '阿里巴巴' LIMIT 1000",
  "explanation": "Query candidates from Alibaba",
  "intent": { "execute": true, "export": true, "exportFormat": "csv" }
}
    ↓
前端根据 intent.execute = true，自动执行查询
    ↓
显示 Loading: "Executing query..."
    ↓
查询完成，显示结果表格
    ↓
前端根据 intent.export = true，自动导出
    ↓
显示 Loading: "Exporting to CSV..."
    ↓
自动下载 CSV 文件
    ↓
显示成功提示: "✓ Found 5 rows and exported to interview_db_2024-01-27.csv"
```

#### 2.6 智能提示（查询后无导出意图时）

当 AI 识别 `intent.export = false` 但查询成功返回数据时，显示提示条：

```
┌──────────────────────────────────────────────────────────┐
│ ✓ Query completed: 50 rows in 45ms                       │
│ Need to export? [Export CSV] [Export JSON] [Dismiss]     │
└──────────────────────────────────────────────────────────┘
```

---

## 技术实现

### 后端改动

#### 文件：`backend/app/services/nl2sql.py`

```python
"""Natural Language to SQL conversion service with intent recognition."""

from openai import AsyncOpenAI
from app.config import settings
from app.models.database import DatabaseType
import json
import logging

logger = logging.getLogger(__name__)


class NaturalLanguageToSQLService:
    """Service for converting natural language queries to SQL with intent recognition."""

    def __init__(self):
        """Initialize OpenAI client."""
        client_kwargs = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            client_kwargs["base_url"] = settings.openai_base_url
        self.client = AsyncOpenAI(**client_kwargs)
        self.model = settings.openai_model

    def _build_prompt(
        self, user_prompt: str, metadata: dict, db_type: DatabaseType = DatabaseType.POSTGRESQL
    ) -> list[dict[str, str]]:
        """Build the prompt for OpenAI with intent recognition."""

        # Build schema context (same as before)
        schema_context = []
        for table in metadata.get("tables", []):
            columns_info = []
            for col in table.get("columns", []):
                col_desc = f"  - {col['name']} ({col['dataType']})"
                if col.get("primaryKey"):
                    col_desc += " PRIMARY KEY"
                columns_info.append(col_desc)
            table_info = f"Table: {table['schemaName']}.{table['name']}\n" + "\n".join(columns_info)
            schema_context.append(table_info)

        schema_text = "\n\n".join(schema_context)

        # Database-specific syntax
        db_name = "MySQL" if db_type == DatabaseType.MYSQL else "PostgreSQL"

        system_message = f"""You are an expert SQL query generator and user intent analyzer for {db_name}.

Database Schema:
{schema_text}

Your tasks:
1. Generate a valid SELECT SQL query based on the user's natural language request
2. Analyze the user's intent regarding query execution and export

SQL Rules:
- Generate ONLY SELECT queries (no INSERT/UPDATE/DELETE/DROP)
- Always include LIMIT clause (max 1000 rows)
- Use proper {db_name} syntax

Intent Analysis Rules:
- execute: Set to true if user wants to see/view results immediately
  Keywords: 执行, 运行, 查一下, 跑一下, 看看, 显示, show, run, execute, get, fetch, display, view
- export: Set to true if user wants to download/save/export the data
  Keywords: 导出, 下载, 保存, 输出, 生成文件, export, download, save, output, to file
- exportFormat:
  - "csv" if user mentions: csv, CSV, 表格, excel, spreadsheet
  - "json" if user mentions: json, JSON
  - "csv" as default if export is true but no format specified
  - null if export is false

IMPORTANT: If user only asks a question without explicit action words, set execute and export to false.

Output format: Return ONLY valid JSON, no markdown, no explanation outside JSON:
{{
  "sql": "SELECT ...",
  "explanation": "Brief description in same language as user input",
  "intent": {{
    "execute": true/false,
    "export": true/false,
    "exportFormat": "csv" | "json" | null
  }}
}}"""

        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_prompt},
        ]

    async def generate_sql(
        self, user_prompt: str, metadata: dict, db_type: DatabaseType = DatabaseType.POSTGRESQL
    ) -> dict:
        """Convert natural language to SQL with intent recognition.

        Returns:
            Dict with 'sql', 'explanation', and 'intent' keys
        """
        try:
            messages = self._build_prompt(user_prompt, metadata, db_type)

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                max_tokens=800,
            )

            content = response.choices[0].message.content.strip()

            # Clean up markdown if present
            if content.startswith("```json"):
                content = content.replace("```json", "").replace("```", "").strip()
            elif content.startswith("```"):
                content = content.replace("```", "").strip()

            # Parse JSON response
            result = json.loads(content)

            # Ensure intent structure exists with defaults
            if "intent" not in result:
                result["intent"] = {
                    "execute": False,
                    "export": False,
                    "exportFormat": None
                }

            logger.info(f"Generated SQL with intent: {result['intent']}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            # Fallback: return just the SQL
            return {
                "sql": content,
                "explanation": f"Generated from: {user_prompt}",
                "intent": {"execute": False, "export": False, "exportFormat": None}
            }
        except Exception as e:
            logger.error(f"Failed to generate SQL: {str(e)}")
            raise Exception(f"Failed to generate SQL: {str(e)}")


nl2sql_service = NaturalLanguageToSQLService()
```

#### 文件：`backend/app/api/v1/queries.py`

更新响应模型：

```python
from pydantic import BaseModel
from typing import Optional, Literal

class IntentResponse(BaseModel):
    execute: bool = False
    export: bool = False
    exportFormat: Optional[Literal["csv", "json"]] = None

class NaturalQueryResponse(BaseModel):
    sql: str
    explanation: str
    intent: IntentResponse
```

### 前端改动

#### 文件：`frontend/src/types/query.ts`

新增类型定义：

```typescript
export interface QueryIntent {
  execute: boolean;
  export: boolean;
  exportFormat: 'csv' | 'json' | null;
}

export interface NaturalQueryResponse {
  sql: string;
  explanation: string;
  intent: QueryIntent;
}
```

#### 文件：`frontend/src/pages/Home.tsx`

```typescript
// 1. 新增状态
const [showFormatModal, setShowFormatModal] = useState(false);
const [showExportHint, setShowExportHint] = useState(false);

// 2. 执行并导出函数（Manual SQL 模式）
const handleExecuteAndExport = async (format?: 'csv' | 'json') => {
  if (!sql.trim()) {
    message.warning("Please enter a SQL query");
    return;
  }

  // 如果没指定格式，弹出选择框
  if (!format) {
    setShowFormatModal(true);
    return;
  }

  setExecuting(true);
  try {
    const response = await apiClient.post<QueryResult>(
      `/api/v1/dbs/${selectedDatabase}/query`,
      { sql: sql.trim() }
    );
    setQueryResult(response.data);

    if (response.data.rows.length === 0) {
      message.info("Query executed successfully but returned no data");
      return;
    }

    // 大数据集警告
    if (response.data.rows.length > 10000) {
      Modal.confirm({
        title: "Large Dataset Warning",
        icon: <ExclamationCircleOutlined />,
        content: `Exporting ${response.data.rowCount.toLocaleString()} rows may take a while. Continue?`,
        onOk: () => doExport(format, response.data),
      });
    } else {
      doExport(format, response.data);
    }
  } catch (error: any) {
    message.error(error.response?.data?.detail || "Query execution failed");
  } finally {
    setExecuting(false);
  }
};

// 3. 导出执行函数
const doExport = (format: 'csv' | 'json', result: QueryResult) => {
  if (format === 'csv') {
    exportToCSV(result);
  } else {
    exportToJSON(result);
  }
  message.success(`Exported ${result.rowCount} rows to ${format.toUpperCase()}`);
};

// 4. 快捷键监听
useEffect(() => {
  const handleKeyDown = (e: KeyboardEvent) => {
    // 仅在 Manual SQL 标签页生效
    if (activeTab !== 'manual') return;

    if ((e.metaKey || e.ctrlKey) && e.shiftKey) {
      if (e.key === 'e' || e.key === 'E') {
        e.preventDefault();
        handleExecuteAndExport();
      } else if (e.key === 'c' || e.key === 'C') {
        e.preventDefault();
        handleExecuteAndExport('csv');
      } else if (e.key === 'j' || e.key === 'J') {
        e.preventDefault();
        handleExecuteAndExport('json');
      }
    }
  };

  window.addEventListener('keydown', handleKeyDown);
  return () => window.removeEventListener('keydown', handleKeyDown);
}, [sql, selectedDatabase, activeTab]);

// 5. 智能查询函数（AI 意图识别）
const handleSmartQuery = async (prompt: string) => {
  if (!selectedDatabase) return;

  setGeneratingSql(true);
  setNlError(null);
  setShowExportHint(false);

  try {
    // 调用后端 AI 服务，获取 SQL + 意图
    const response = await apiClient.post<NaturalQueryResponse>(
      `/api/v1/dbs/${selectedDatabase}/query/natural`,
      { prompt }
    );

    const { sql: generatedSql, intent } = response.data;
    setSql(generatedSql);

    // 根据 AI 识别的意图执行操作
    if (intent.execute) {
      // 自动执行查询
      const queryResponse = await apiClient.post<QueryResult>(
        `/api/v1/dbs/${selectedDatabase}/query`,
        { sql: generatedSql }
      );
      setQueryResult(queryResponse.data);
      setActiveTab("manual");

      if (intent.export && queryResponse.data.rows.length > 0) {
        // 自动导出
        const format = intent.exportFormat || 'csv';

        // 大数据集警告
        if (queryResponse.data.rows.length > 10000) {
          Modal.confirm({
            title: "Large Dataset Warning",
            icon: <ExclamationCircleOutlined />,
            content: `Exporting ${queryResponse.data.rowCount.toLocaleString()} rows. Continue?`,
            onOk: () => doExport(format, queryResponse.data),
          });
        } else {
          doExport(format, queryResponse.data);
        }

        message.success(
          `Found ${queryResponse.data.rowCount} rows and exported to ${format.toUpperCase()}`
        );
      } else if (queryResponse.data.rows.length > 0) {
        // 执行成功但无导出意图，显示导出提示
        setShowExportHint(true);
        message.success(`Query executed: ${queryResponse.data.rowCount} rows in ${queryResponse.data.executionTimeMs}ms`);
      } else {
        message.info("Query executed but returned no data");
      }
    } else {
      // 仅生成 SQL，不执行
      setActiveTab("manual");
      message.success("SQL generated! You can review, edit and execute it.");
    }
  } catch (error: any) {
    const errorMsg = error.response?.data?.detail || "Failed to process query";
    setNlError(errorMsg);
    message.error(errorMsg);
  } finally {
    setGeneratingSql(false);
  }
};
```

#### 导出提示条组件

```tsx
{/* 查询完成后的导出提示 */}
{showExportHint && queryResult && queryResult.rows.length > 0 && (
  <Alert
    message={
      <Space>
        <span>Query completed: {queryResult.rowCount} rows</span>
        <Button size="small" onClick={() => { handleExportCSV(); setShowExportHint(false); }}>
          Export CSV
        </Button>
        <Button size="small" onClick={() => { handleExportJSON(); setShowExportHint(false); }}>
          Export JSON
        </Button>
      </Space>
    }
    type="success"
    closable
    onClose={() => setShowExportHint(false)}
    style={{ marginBottom: 16 }}
  />
)}
```

#### 文件：`frontend/src/components/NaturalLanguageInput.tsx`

```tsx
import React, { useState } from "react";
import { Input, Button, Space, Alert, Typography } from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";

const { TextArea } = Input;
const { Text } = Typography;

interface NaturalLanguageInputProps {
  onGenerateSQL: (prompt: string) => void;
  loading?: boolean;
  error?: string | null;
}

export const NaturalLanguageInput: React.FC<NaturalLanguageInputProps> = ({
  onGenerateSQL,
  loading = false,
  error = null,
}) => {
  const [prompt, setPrompt] = useState("");

  const handleSubmit = () => {
    if (prompt.trim()) {
      onGenerateSQL(prompt.trim());
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <TextArea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={`Describe what you want to query in natural language...

Examples:
• "查询所有部门" - Generate SQL only
• "看看有多少候选人" - Generate and execute
• "导出所有职位信息为CSV" - Generate, execute, and export

Supports both English and Chinese.`}
        rows={5}
        disabled={loading}
      />

      {error && (
        <Alert message={error} type="error" showIcon closable />
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          Press Cmd/Ctrl + Enter to submit
        </Text>
        <Button
          type="primary"
          icon={<ThunderboltOutlined />}
          onClick={handleSubmit}
          loading={loading}
          disabled={!prompt.trim()}
          size="large"
          style={{ fontWeight: 700 }}
        >
          SMART QUERY
        </Button>
      </div>
    </Space>
  );
};
```

### UI 组件

#### 格式选择弹窗

```tsx
<Modal
  title="Select Export Format"
  open={showFormatModal}
  onCancel={() => setShowFormatModal(false)}
  footer={null}
  centered
>
  <Space direction="vertical" style={{ width: '100%' }} size="middle">
    <Button
      block
      size="large"
      icon={<FileTextOutlined />}
      onClick={() => {
        setShowFormatModal(false);
        handleExecuteAndExport('csv');
      }}
    >
      Export as CSV
    </Button>
    <Button
      block
      size="large"
      icon={<FileTextOutlined />}
      onClick={() => {
        setShowFormatModal(false);
        handleExecuteAndExport('json');
      }}
    >
      Export as JSON
    </Button>
  </Space>
</Modal>
```

#### 执行并导出按钮组

```tsx
<Space.Compact>
  <Button
    type="primary"
    icon={<PlayCircleOutlined />}
    onClick={handleExecuteQuery}
    loading={executing}
  >
    EXECUTE
  </Button>
  <Dropdown
    menu={{
      items: [
        { key: 'csv', label: 'Execute & Export CSV', icon: <DownloadOutlined /> },
        { key: 'json', label: 'Execute & Export JSON', icon: <DownloadOutlined /> },
      ],
      onClick: ({ key }) => handleExecuteAndExport(key as 'csv' | 'json'),
    }}
  >
    <Button type="primary" icon={<DownloadOutlined />} />
  </Dropdown>
</Space.Compact>
```

---

## 测试用例

### Manual SQL 模式

| 测试场景 | 操作 | 预期结果 |
|----------|------|----------|
| 正常导出 CSV | 输入有效 SQL，按 Cmd+Shift+C | 执行查询，下载 CSV 文件 |
| 正常导出 JSON | 输入有效 SQL，按 Cmd+Shift+J | 执行查询，下载 JSON 文件 |
| 格式选择 | 输入有效 SQL，按 Cmd+Shift+E | 弹出格式选择框 |
| SQL 为空 | 不输入 SQL，按 Cmd+Shift+E | 显示警告提示 |
| SQL 错误 | 输入错误 SQL，按 Cmd+Shift+E | 显示错误信息，不导出 |
| 结果为空 | 查询返回 0 行，按 Cmd+Shift+E | 执行成功，提示无数据 |
| 大数据集 | 查询返回 >10000 行 | 显示警告确认框 |

### Natural Language 模式

| 测试输入 | 预期行为 |
|----------|----------|
| "查询所有部门" | 生成 SQL，切换到 Manual 标签页 |
| "执行查询所有部门" | 生成 SQL → 执行 → 显示结果 |
| "导出所有候选人为CSV" | 生成 SQL → 执行 → 下载 CSV |
| "下载职位列表JSON格式" | 生成 SQL → 执行 → 下载 JSON |
| "看看有多少面试官" | 生成 SQL → 执行 → 显示结果 |

---

## 快捷键速查表

| 快捷键 | 功能 | 适用模式 |
|--------|------|----------|
| `Cmd/Ctrl + Enter` | 执行查询 / 智能查询 | 两种模式 |
| `Cmd/Ctrl + Shift + E` | 执行并导出（选择格式） | Manual SQL |
| `Cmd/Ctrl + Shift + C` | 执行并导出 CSV | Manual SQL |
| `Cmd/Ctrl + Shift + J` | 执行并导出 JSON | Manual SQL |

---

## 实施计划

### Phase 1: Manual SQL 执行并导出
- [ ] 添加 `handleExecuteAndExport` 函数
- [ ] 添加格式选择弹窗组件
- [ ] 实现快捷键监听 (Cmd+Shift+E/C/J)
- [ ] 添加异常处理逻辑
- [ ] 更新按钮 UI（下拉按钮组）
- [ ] 测试所有场景

### Phase 2: 后端 AI 意图识别
- [ ] 更新 `nl2sql.py` 的 System Prompt，增加意图识别
- [ ] 修改 API 响应结构，增加 `intent` 字段
- [ ] 更新 Pydantic 响应模型
- [ ] 处理 JSON 解析异常，提供 fallback
- [ ] 测试各种自然语言输入的意图识别

### Phase 3: 前端智能查询
- [ ] 新增 `NaturalQueryResponse` 类型定义
- [ ] 更新 `handleSmartQuery` 函数，根据 intent 执行操作
- [ ] 修改按钮文案为 "SMART QUERY"
- [ ] 添加导出提示条组件
- [ ] 集成大数据集警告
- [ ] 测试端到端流程

### Phase 4: 优化与增强
- [ ] 优化 loading 状态提示文案
- [ ] 添加操作历史记录（可选）
- [ ] 性能优化
- [ ] 用户体验细节打磨

---

## 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2024-01-27 | 初始功能设计文档 |
| v1.1 | 2024-01-27 | 意图识别改为后端 AI 驱动，更新技术实现方案 |
