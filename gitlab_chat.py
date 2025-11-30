#!/usr/bin/env python3
"""GitLab MCP Chat with PolyMCP"""
import asyncio
from polymcp.polyagent import UnifiedPolyAgent, OllamaProvider

async def main():
    # Configure the LLM provider (you can use OpenAI, Anthropic, Ollama, etc.)
    llm = OllamaProvider(model="gpt-oss:120b-cloud", temperature=0.1)
    
    # Point to the GitLab MCP server
    mcp_servers = ["http://localhost:8000/mcp"]
    
    agent = UnifiedPolyAgent(
        llm_provider=llm, 
        mcp_servers=mcp_servers,  
        verbose=True
    )
    
    async with agent:
        print("\nGitLab MCP Server connected!\n")
        print("Available commands:")
        print("- 'show me open merge requests'")
        print("- 'analyze failed jobs'")
        print("- 'create an ADR for cloud migration'")
        print("- 'exit' to quit\n")
        
        while True:
            user_input = input("\nYou: ")
            
            if user_input.lower() in ['exit', 'quit']:
                print("Session ended.")
                break
            
            result = await agent.run_async(user_input, max_steps=5)
            print(f"\nGitLab Assistant: {result}")

if __name__ == "__main__":
    asyncio.run(main())
