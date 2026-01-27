/** Natural language query input component with smart intent recognition. */

import React, { useState } from "react";
import { Input, Button, Space, Typography, Alert } from "antd";
import { ThunderboltOutlined, LoadingOutlined } from "@ant-design/icons";

const { TextArea } = Input;
const { Text } = Typography;

interface NaturalLanguageInputProps {
  onSmartQuery: (prompt: string) => void;
  loading?: boolean;
  error?: string | null;
}

export const NaturalLanguageInput: React.FC<NaturalLanguageInputProps> = ({
  onSmartQuery,
  loading = false,
  error = null,
}) => {
  const [prompt, setPrompt] = useState("");

  const handleSubmit = () => {
    if (prompt.trim()) {
      onSmartQuery(prompt.trim());
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Submit on Cmd/Ctrl + Enter
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={12}>
      <div>
        <Text strong style={{ fontSize: 13, textTransform: "uppercase" }}>
          Describe your query in natural language
        </Text>
        <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
          (English or Chinese)
        </Text>
      </div>

      <TextArea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={`Examples:
• "查询所有部门" - Generate SQL only
• "看看有多少候选人" - Generate and execute
• "导出所有职位信息为CSV" - Generate, execute, and export

AI will automatically detect your intent.`}
        rows={5}
        style={{
          fontSize: 14,
          borderWidth: 2,
          borderRadius: 2,
        }}
        disabled={loading}
      />

      {error && (
        <Alert
          message="Query Failed"
          description={error}
          type="error"
          closable
          style={{ borderWidth: 2 }}
        />
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          Press Cmd/Ctrl + Enter to submit
        </Text>
        <Button
          type="primary"
          icon={loading ? <LoadingOutlined /> : <ThunderboltOutlined />}
          onClick={handleSubmit}
          loading={loading}
          disabled={!prompt.trim() || loading}
          size="large"
          style={{
            height: 40,
            paddingLeft: 20,
            paddingRight: 20,
            fontWeight: 700,
          }}
        >
          SMART QUERY
        </Button>
      </div>
    </Space>
  );
};
