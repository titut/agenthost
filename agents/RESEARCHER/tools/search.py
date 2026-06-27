"""Search tools for the RESEARCHER agent."""
from __future__ import annotations


def search_web(query: str, max_results: int = 5) -> dict:
    """Search the web using DuckDuckGo and return top results.

    No API key required. Returns a list of results with title, href, and body snippet.
    """
    try:
        from ddgs import DDGS
    except ImportError as exc:
        return {"error": f"ddgs is not installed: {exc}"}

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return {
            "query": query,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in results
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Search failed: {type(exc).__name__}: {exc}"}


def calculate(expression: str) -> dict:
    """Evaluate a simple math expression safely.

    Supports +, -, *, /, parentheses, and decimal numbers.
    """
    import ast
    import operator

    allowed = {
        ast.Expression: None,
        ast.BinOp: None,
        ast.UnaryOp: None,
        ast.Num: None,
        ast.Constant: None,
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.USub: operator.neg,
        ast.Pow: operator.pow,
    }

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in allowed or allowed[op_type] is None:
                raise ValueError(f"Unsupported operator: {op_type.__name__}")
            return allowed[op_type](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in allowed or allowed[op_type] is None:
                raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
            return allowed[op_type](_eval(node.operand))
        raise ValueError(f"Unsupported node: {type(node).__name__}")

    try:
        tree = ast.parse(expression, mode="eval")
        return {"expression": expression, "result": _eval(tree)}
    except Exception as exc:
        return {"error": str(exc)}
