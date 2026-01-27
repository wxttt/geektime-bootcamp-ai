"""Natural Language to SQL conversion service with AI intent recognition."""

import json
import logging

from openai import AsyncOpenAI

from app.config import settings
from app.models.database import DatabaseType

logger = logging.getLogger(__name__)


class NaturalLanguageToSQLService:
    """Service for converting natural language queries to SQL with intent recognition."""

    def __init__(self):
        """Initialize OpenAI client with optional custom base URL."""
        client_kwargs = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            client_kwargs["base_url"] = settings.openai_base_url
        self.client = AsyncOpenAI(**client_kwargs)
        self.model = settings.openai_model

    def _build_prompt(
        self, user_prompt: str, metadata: dict, db_type: DatabaseType = DatabaseType.POSTGRESQL
    ) -> list[dict[str, str]]:
        """Build the prompt for OpenAI with intent recognition.

        Args:
            user_prompt: Natural language query from user
            metadata: Database schema metadata dictionary
            db_type: Database type (PostgreSQL or MySQL)

        Returns:
            List of messages for OpenAI chat completion
        """
        # Build schema context
        schema_context = []
        for table in metadata.get("tables", []):
            columns_info = []
            for col in table.get("columns", []):
                col_desc = f"  - {col['name']} ({col['dataType']})"
                if col.get("primaryKey"):
                    col_desc += " PRIMARY KEY"
                if not col.get("nullable", True):
                    col_desc += " NOT NULL"
                if col.get("unique"):
                    col_desc += " UNIQUE"
                columns_info.append(col_desc)

            row_count = table.get("rowCount", "unknown")
            table_info = f"Table: {table['schemaName']}.{table['name']} ({row_count} rows)\n"
            table_info += "\n".join(columns_info)
            schema_context.append(table_info)

        for view in metadata.get("views", []):
            columns_info = [
                f"  - {col['name']} ({col['dataType']})" for col in view.get("columns", [])
            ]
            view_info = f"View: {view['schemaName']}.{view['name']}\n"
            view_info += "\n".join(columns_info)
            schema_context.append(view_info)

        schema_text = "\n\n".join(schema_context)

        # Build database-specific rules
        if db_type == DatabaseType.MYSQL:
            db_name = "MySQL"
            syntax_rules = """- Use backticks for identifiers (e.g., `table_name`)
- Use MySQL LIMIT syntax"""
        else:
            db_name = "PostgreSQL"
            syntax_rules = """- Use proper schema qualification (schema.table)
- Use double quotes for identifiers if needed"""

        system_message = f"""You are an expert SQL query generator and user intent analyzer for {db_name}.

Database Schema:
{schema_text}

Your tasks:
1. Generate a valid SELECT SQL query based on the user's natural language request
2. Analyze the user's intent regarding query execution and export

SQL Rules:
- Generate ONLY SELECT queries (no INSERT/UPDATE/DELETE/DROP)
- Always include LIMIT clause (max 1000 rows)
{syntax_rules}
- Handle both English and Chinese natural language

Intent Analysis Rules:
- execute: Set to true if user wants to see/view/run results immediately
  Keywords indicating execute: 执行, 运行, 查一下, 跑一下, 看看, 显示, 查询一下, show, run, execute, get, fetch, display, view, find
- export: Set to true if user wants to download/save/export the data to a file
  Keywords indicating export: 导出, 下载, 保存, 输出, 生成文件, export, download, save, output, to file, 保存为
- exportFormat:
  - "csv" if user mentions: csv, CSV, 表格, excel, spreadsheet, 电子表格
  - "json" if user mentions: json, JSON
  - "csv" as default if export is true but no format specified
  - null if export is false

IMPORTANT:
- If user only asks a descriptive question without action words, set execute=false and export=false
- If user wants to export, execute must also be true (can't export without executing)

Output format: Return ONLY valid JSON (no markdown code blocks):
{{"sql": "SELECT ...", "explanation": "Brief description in same language as user input", "intent": {{"execute": true/false, "export": true/false, "exportFormat": "csv" | "json" | null}}}}"""

        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_prompt},
        ]

    async def generate_sql(
        self, user_prompt: str, metadata: dict, db_type: DatabaseType = DatabaseType.POSTGRESQL
    ) -> dict:
        """Convert natural language to SQL with intent recognition.

        Args:
            user_prompt: Natural language query
            metadata: Database schema metadata dictionary
            db_type: Database type (PostgreSQL or MySQL)

        Returns:
            Dict with 'sql', 'explanation', and 'intent' keys

        Raises:
            Exception: If OpenAI API call fails
        """
        try:
            messages = self._build_prompt(user_prompt, metadata, db_type)

            # Call OpenAI API
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
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                # Fallback: try to extract SQL from non-JSON response
                logger.warning(f"Failed to parse AI response as JSON, using fallback: {content[:100]}")
                result = {
                    "sql": content,
                    "explanation": f"Generated from: {user_prompt}",
                    "intent": {"execute": False, "export": False, "exportFormat": None},
                }

            # Ensure intent structure exists with defaults
            if "intent" not in result:
                result["intent"] = {"execute": False, "export": False, "exportFormat": None}
            else:
                # Ensure all intent fields exist
                result["intent"].setdefault("execute", False)
                result["intent"].setdefault("export", False)
                result["intent"].setdefault("exportFormat", None)

            # If export is true, execute must also be true
            if result["intent"]["export"]:
                result["intent"]["execute"] = True

            logger.info(
                f"Generated SQL with intent: execute={result['intent']['execute']}, "
                f"export={result['intent']['export']}, format={result['intent']['exportFormat']}"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to generate SQL: {str(e)}")
            raise Exception(f"Failed to generate SQL: {str(e)}")


# Global instance
nl2sql_service = NaturalLanguageToSQLService()
