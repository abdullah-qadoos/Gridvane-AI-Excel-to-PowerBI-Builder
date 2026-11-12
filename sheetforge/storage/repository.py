from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from .models import Project, Run, Setting


def now():
    return datetime.now(UTC).isoformat()


def row_dict(item):
    return (
        {column.name: getattr(item, column.name) for column in item.__table__.columns}
        if item
        else None
    )


class Repository:
    def __init__(self, home):
        self.home = Path(home).resolve()
        (self.home / "projects").mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            "sqlite:///" + str(self.home / "sheetforge.db"),
            connect_args={"timeout": 30},
            poolclass=NullPool,
            echo=False,
        )

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with Session(self.engine) as session, session.begin():
            session.execute(
                update(Run)
                .where(
                    Run.status.in_(["planning", "applying", "checking", "modeling", "generating"])
                )
                .values(status="interrupted")
            )

    def project(self, project_id):
        with Session(self.engine) as session:
            return row_dict(session.get(Project, str(project_id)))

    def projects(self):
        with Session(self.engine) as session:
            return [
                row_dict(item)
                for item in session.scalars(
                    select(Project).order_by(Project.created_at.desc()).limit(100)
                )
            ]

    def add_project(self, project_id, name, source):
        with Session(self.engine) as session, session.begin():
            session.add(
                Project(id=project_id, name=name, source_path=str(source), created_at=now())
            )

    def add_run(self, project_id):
        run_id = str(uuid4())
        with Session(self.engine) as session, session.begin():
            session.add(
                Run(
                    id=run_id,
                    project_id=str(project_id),
                    status="uploaded",
                    created_at=now(),
                    steps_json="[]",
                )
            )
        return self.run(run_id)

    def run(self, run_id):
        with Session(self.engine) as session:
            return row_dict(session.get(Run, str(run_id)))

    def runs(self, project_id):
        with Session(self.engine) as session:
            return [
                row_dict(item)
                for item in session.scalars(
                    select(Run)
                    .where(Run.project_id == str(project_id))
                    .order_by(Run.created_at.desc())
                )
            ]

    def update_run(self, run_id, **values):
        allowed = {"cleaning_plan_json", "model_plan_json", "steps_json", "output_path", "status"}
        if not set(values) <= allowed:
            raise ValueError("Unsupported run metadata field.")
        with Session(self.engine) as session, session.begin():
            session.execute(update(Run).where(Run.id == str(run_id)).values(**values))
        return self.run(run_id)

    def setting(self, key, default=None):
        with Session(self.engine) as session:
            item = session.get(Setting, key)
            return item.value if item else default

    def set_setting(self, key, value):
        if key != "llm_config":
            raise ValueError("Unsupported setting key.")
        import json

        data = json.loads(value)
        if not set(data) <= {"provider", "model", "base_url", "api_version"}:
            raise ValueError("Only non-secret model configuration may be persisted.")
        with Session(self.engine) as session, session.begin():
            session.merge(Setting(key=key, value=value))
