from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import JSON, TypeDecorator
from onestep import step
from onestep.broker import MemoryBroker
from onestep.broker.mysql import MysqlBroker, Model, Field
from onestep.retry import AdvancedRetry

todo_broker = MemoryBroker()

class JSONEncodedDict(TypeDecorator):
    """自定义 JSON 类型装饰器，处理复杂对象序列化"""

    impl = JSON

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is not None:
            return self._convert_for_json(value)
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return value

    @staticmethod
    def _convert_for_json(obj: Any) -> Any:
        if isinstance(obj, (list, tuple)):
            return [JSONEncodedDict._convert_for_json(v) for v in obj]
        elif isinstance(obj, dict):
            return {k: JSONEncodedDict._convert_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, UUID):
            return str(obj)
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif hasattr(obj, "dict"):  # 处理 Pydantic/SQLModel 模型
            return JSONEncodedDict._convert_for_json(obj.dict())
        return obj


class ItemModel(Model, table=True):
    __tablename__ = "item"  # pyright: ignore[reportAssignmentType]
    
    id: int = Field(primary_key=True)
    title: str
    content: str
    status: str
    ids: Optional[List[int]] = Field(
        default=None,
        sa_type=JSONEncodedDict,
        sa_column_kwargs={"comment": "ID列表"},
    )
    meta: Optional[Dict] = Field(
        default=None, sa_type=JSONEncodedDict, sa_column_kwargs={"comment": "原始数据"}
    )
    created_at: datetime = Field(default_factory=datetime.now)


mysql_broker = MysqlBroker(params={
    "uri": "mysql+pymysql://root:root@localhost:3306/onestep"
})
@step(to_broker=todo_broker)
def build_todo_list():
    # mock data
    yield from [
        {
            "id": 1,
            "title": "todo1",
            "content": "todo1 content",
            "status": "todo",
            "ids": [1, 2, 3],
            "meta": {"key": "value"},
            "created_at": "2023-01-01 00:00:00",
        },
        {
            "id": 2,
            "title": "todo2",
            "content": "todo2 content",
            "status": "todo",
            "ids": [4, 5, 6],
            "meta": {"key": "value"},
            "created_at": "2023-01-02 00:00:00",    
        },
        {
            "id": 3,
            "title": "todo3",
            "content": "todo3 content",
            "status": "todo",
            "ids": [7, 8, 9],
            "meta": {"key": "value"},
            "created_at": "2023-01-03 00:00:00",    
        },
    ]


@step(from_broker=todo_broker, workers=1,
      to_broker=mysql_broker,
      retry=AdvancedRetry()
      )
def do_something(todo):
    todo.body["status"] = "done"
    if isinstance(todo.body.get("created_at"), str):
        try:
            todo.body["created_at"] = datetime.fromisoformat(todo.body["created_at"])  # noqa: F401
        except ValueError:
            todo.body["created_at"] = datetime.strptime(todo.body["created_at"], "%Y-%m-%d %H:%M:%S")
    return ItemModel(**todo.body)


if __name__ == '__main__':
    import time
    step.set_debugging()

    build_todo_list()
    step.start(block=False)
    time.sleep(5)
    step.shutdown()
