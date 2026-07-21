from mcp.server.fastmcp import FastMCP


server = FastMCP("fixture-mcp", log_level="ERROR")


if __name__ == "__main__":
    server.run(transport="stdio")
