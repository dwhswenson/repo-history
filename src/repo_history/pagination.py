"""Independent forward connection traversal, validating pages before yielding."""

from collections.abc import Mapping

from .errors import PaginationError


def iter_connection(
    client,
    document,
    variables,
    *,
    operation_name,
    connection_path,
    cursor_variable,
    page_size_variable,
    page_size,
):
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer between 1 and 100")
    if (
        not isinstance(connection_path, (tuple, list))
        or not connection_path
        or any(not isinstance(key, str) or not key for key in connection_path)
    ):
        raise ValueError("connection_path must be a nonempty sequence of field names")
    if (
        not isinstance(cursor_variable, str)
        or not cursor_variable
        or not isinstance(page_size_variable, str)
        or not page_size_variable
        or cursor_variable == page_size_variable
    ):
        raise ValueError("cursor and page size variable names must be distinct")
    if not isinstance(variables, Mapping):
        raise ValueError("variables must be a mapping")
    values = dict(variables)
    cursor = values.get(cursor_variable)
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise ValueError("initial cursor must be a nonempty string or null")
    seen = {cursor} if cursor is not None else set()
    while True:
        values.update({cursor_variable: cursor, page_size_variable: page_size})
        data, page_size = client._execute(
            document,
            values,
            operation_name=operation_name,
            page_size_variable=page_size_variable,
        )
        connection = data
        for key in connection_path:
            if not isinstance(connection, dict) or key not in connection:
                raise PaginationError(
                    "missing connection path", operation=operation_name
                )
            connection = connection[key]
        if not isinstance(connection, dict):
            raise PaginationError(
                "missing or malformed connection", operation=operation_name
            )
        nodes = connection.get("nodes")
        info = connection.get("pageInfo")
        if (
            not isinstance(nodes, list)
            or any(n is not None and not isinstance(n, dict) for n in nodes)
            or not isinstance(info, dict)
            or type(info.get("hasNextPage")) is not bool
            or "endCursor" not in info
            or (
                info["endCursor"] is not None
                and (not isinstance(info["endCursor"], str) or not info["endCursor"])
            )
        ):
            raise PaginationError(
                "malformed nodes or pageInfo", operation=operation_name
            )
        next_cursor = info["endCursor"]
        if info["hasNextPage"] and (next_cursor is None or next_cursor in seen):
            raise PaginationError(
                "missing or repeated continuation cursor", operation=operation_name
            )
        yield from nodes
        if not info["hasNextPage"]:
            return
        seen.add(next_cursor)
        cursor = next_cursor
