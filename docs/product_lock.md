# Product Lock

AgentAtlas is an agent-maintained, orchestrator-verified knowledge graph for AI-agent tool intelligence.

The product exists so an AI agent can ask what a tool can do, how a command or API behaves, what evidence supports that knowledge, and whether the action is safe before it acts.

## Product Identity

- Project name: `AgentAtlas`
- Product category: AI-agent infrastructure, tool intelligence, agent safety, MCP/tool governance, executable knowledge graph
- Primary user: AI agents that need reliable structured knowledge before using tools
- Secondary users: developers, AI engineers, DevOps teams, security teams, maintainers, MCP server creators, and enterprise agent-platform teams

## Product Thesis

Most agents can call tools before they understand the tool's behavior, side effects, evidence quality, or operational risk.

AgentAtlas closes that gap by requiring specialized agents and maintainers to submit structured, evidence-backed claims. A Canon Orchestrator verifies those claims before publishing accepted knowledge into canonical `ToolSpec` and `WorkflowSpec` objects.

## North Star Flow

```text
Specialized Agent -> KnowledgeClaim -> Evidence -> Canon Orchestrator -> Verification -> ToolSpec/WorkflowSpec -> Agent Query API/MCP
```

Every stage of the system must strengthen this flow.

## Initial Tool Scope

The first supported tools are:

- `git`
- `github-cli`
- `docker`
- `vercel-cli`
- `openai-api`

Do not expand beyond these tools until the trust core can accept, reject, defer, and explain claims for them with evidence.

## Non-Negotiable Product Principles

- Evidence before publication
- Structured claims over prose
- Safety is first-class
- Verification levels are explicit
- APIs and MCP tools are agent-readable by design
- Narrow tool scope before broad coverage
- No LLM-only canonical knowledge
- No hidden confidence inflation
- No unsafe runtime verification

## Anti-Goals

- AgentAtlas is not a generic chatbot
- AgentAtlas is not a documentation search engine
- AgentAtlas is not a prose wiki
- AgentAtlas is not a broad crawler before verification works
- AgentAtlas is not a UI-first dashboard project
- AgentAtlas is not a graph database demo without trustworthy inputs

## Stage 0 Exit Standard

Stage 0 is complete only when the project has a fixed vocabulary, fixed taxonomies, fixed verification semantics, fixed risk semantics, and concrete demo scenarios that later stages can test against.
