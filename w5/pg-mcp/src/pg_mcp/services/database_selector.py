"""Database selector for intelligent database routing.

This module provides LLM-based database selection based on user questions
and database descriptions. It analyzes the question content and matches
it against configured database descriptions to select the most appropriate
database for query execution.
"""

import json
import logging
import re
from dataclasses import dataclass

from openai import AsyncOpenAI

from pg_mcp.config.settings import OpenAIConfig

logger = logging.getLogger(__name__)


@dataclass
class SelectionResult:
    """Result of database selection.

    Attributes:
        database: Selected database name.
        confidence: Confidence score (0.0 to 1.0).
        reason: Explanation for the selection.
    """

    database: str
    confidence: float
    reason: str


class DatabaseSelector:
    """Selects appropriate database based on user question and database descriptions.

    This class uses LLM to analyze the user's question and match it against
    database descriptions to select the most appropriate database.

    Example:
        >>> selector = DatabaseSelector(openai_config)
        >>> result = await selector.select(
        ...     question="显示所有已发布的文章",
        ...     database_descriptions={
        ...         "blog_small": "博客系统，包含文章(posts)、评论(comments)、用户(users)表",
        ...         "ecommerce_medium": "电商系统，包含商品(products)、订单(orders)、客户表"
        ...     }
        ... )
        >>> print(result.database)  # "blog_small"
        >>> print(result.confidence)  # 0.95
    """

    SYSTEM_PROMPT = """你是一个数据库路由助手。根据用户的问题和可用数据库的描述，选择最合适的数据库。

可用数据库:
{databases}

请分析用户问题，选择最匹配的数据库。返回 JSON 格式：
{{
    "database": "选中的数据库名",
    "confidence": 0.0到1.0之间的置信度,
    "reason": "选择原因的简短说明"
}}

选择原则：
1. 如果问题明确涉及某个数据库的内容（如"文章"对应博客系统），选择该数据库
2. 分析问题中的关键词，匹配数据库描述中的表名或业务领域
3. 如果无法确定，选择最可能相关的数据库，并给出较低的置信度

只返回 JSON，不要其他文字。"""

    def __init__(self, config: OpenAIConfig) -> None:
        """Initialize database selector.

        Args:
            config: OpenAI configuration for LLM calls.
        """
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,
            timeout=config.timeout,
        )

    async def select(
        self,
        question: str,
        database_descriptions: dict[str, str],
    ) -> SelectionResult:
        """Select the most appropriate database for a question.

        Args:
            question: User's natural language question.
            database_descriptions: Dict mapping database names to descriptions.

        Returns:
            SelectionResult: The selected database with confidence and reason.

        Raises:
            ValueError: If no databases are provided.
        """
        if not database_descriptions:
            raise ValueError("No databases available for selection")

        # If only one database, return it directly without LLM call
        if len(database_descriptions) == 1:
            db_name = next(iter(database_descriptions.keys()))
            logger.debug(f"Single database available, selecting: {db_name}")
            return SelectionResult(
                database=db_name,
                confidence=1.0,
                reason="Only one database available",
            )

        # Build database description text
        db_text = "\n".join(
            f"- {name}: {desc}" for name, desc in database_descriptions.items()
        )

        system_prompt = self.SYSTEM_PROMPT.format(databases=db_text)

        try:
            response = await self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"用户问题: {question}"},
                ],
                temperature=0.0,
                max_tokens=200,
            )

            content = response.choices[0].message.content or ""
            result = self._parse_response(content, database_descriptions)

            logger.info(
                "Database selected",
                extra={
                    "question": question[:100],
                    "selected_database": result.database,
                    "confidence": result.confidence,
                    "reason": result.reason,
                },
            )

            return result

        except Exception as e:
            logger.warning(f"Database selection failed: {e}, using first database")
            # Fallback: use first database
            db_name = next(iter(database_descriptions.keys()))
            return SelectionResult(
                database=db_name,
                confidence=0.5,
                reason=f"Selection failed ({type(e).__name__}), fallback to first database",
            )

    def _parse_response(
        self,
        content: str,
        available_databases: dict[str, str],
    ) -> SelectionResult:
        """Parse LLM response to SelectionResult.

        Args:
            content: Raw LLM response content.
            available_databases: Available database names for validation.

        Returns:
            SelectionResult: Parsed selection result.
        """
        try:
            # Try to extract JSON from response
            json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())

                database = data.get("database", "")

                # Validate database name exists
                if database not in available_databases:
                    # Try fuzzy matching
                    for db_name in available_databases:
                        if (
                            db_name.lower() in database.lower()
                            or database.lower() in db_name.lower()
                        ):
                            database = db_name
                            break
                    else:
                        # No match found, use first database
                        database = next(iter(available_databases.keys()))
                        logger.warning(
                            f"LLM returned unknown database '{data.get('database')}', "
                            f"falling back to '{database}'"
                        )

                confidence = float(data.get("confidence", 0.8))
                # Clamp confidence to valid range
                confidence = max(0.0, min(1.0, confidence))

                return SelectionResult(
                    database=database,
                    confidence=confidence,
                    reason=data.get("reason", "Selected by LLM"),
                )

        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse LLM response: {e}, content: {content[:200]}")

        # Parse failed, use first database
        return SelectionResult(
            database=next(iter(available_databases.keys())),
            confidence=0.5,
            reason="Failed to parse LLM response",
        )
