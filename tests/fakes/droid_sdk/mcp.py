def tool(name, description):
    def wrap(fn):
        fn.tool_name = name
        return fn
    return wrap


def create_sdk_mcp_server(name, tools):
    return {"name": name, "tools": tools}
