import datetime
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pandas as pd
import tomli
import tomli_w
from matplotlib import pyplot as plt
from pydantic import (
    DirectoryPath,
    Field,
    FilePath,
    field_serializer,
    model_validator,
)

import ribasim
from ribasim.config import (
    Allocation,
    Basin,
    DiscreteControl,
    FlowBoundary,
    FractionalFlow,
    LevelBoundary,
    LevelDemand,
    LinearResistance,
    Logging,
    ManningResistance,
    MultiNodeModel,
    Outlet,
    PidControl,
    Pump,
    Results,
    Solver,
    TabulatedRatingCurve,
    Terminal,
    UserDemand,
)
from ribasim.geometry.edge import EdgeTable
from ribasim.geometry.node import NodeTable
from ribasim.input_base import (
    ChildModel,
    FileModel,
    context_file_loading,
)


class Model(FileModel):
    starttime: datetime.datetime
    endtime: datetime.datetime

    input_dir: Path = Field(default_factory=lambda: Path("."))
    results_dir: Path = Field(default_factory=lambda: Path("results"))

    allocation: Allocation = Field(default_factory=Allocation)
    logging: Logging = Field(default_factory=Logging)
    solver: Solver = Field(default_factory=Solver)
    results: Results = Field(default_factory=Results)

    basin: Basin = Field(default_factory=Basin)
    linear_resistance: LinearResistance = Field(default_factory=LinearResistance)
    manning_resistance: ManningResistance = Field(default_factory=ManningResistance)
    tabulated_rating_curve: TabulatedRatingCurve = Field(
        default_factory=TabulatedRatingCurve
    )
    fractional_flow: FractionalFlow = Field(default_factory=FractionalFlow)
    pump: Pump = Field(default_factory=Pump)
    level_boundary: LevelBoundary = Field(default_factory=LevelBoundary)
    flow_boundary: FlowBoundary = Field(default_factory=FlowBoundary)
    outlet: Outlet = Field(default_factory=Outlet)
    terminal: Terminal = Field(default_factory=Terminal)
    discrete_control: DiscreteControl = Field(default_factory=DiscreteControl)
    pid_control: PidControl = Field(default_factory=PidControl)
    user_demand: UserDemand = Field(default_factory=UserDemand)
    level_demand: LevelDemand = Field(default_factory=LevelDemand)

    edge: EdgeTable = Field(default_factory=EdgeTable)

    @model_validator(mode="after")
    def set_node_parent(self) -> "Model":
        for (
            k,
            v,
        ) in self._children().items():
            setattr(v, "_parent", self)
            setattr(v, "_parent_field", k)
        return self

    @field_serializer("input_dir", "results_dir")
    def serialize_path(self, path: Path) -> str:
        return str(path)

    def model_post_init(self, __context: Any) -> None:
        # Always write dir fields
        self.model_fields_set.update({"input_dir", "results_dir"})

    def __repr__(self) -> str:
        """Generate a succinct overview of the Model content.

        Skip "empty" NodeModel instances: when all dataframes are None.
        """
        content = ["ribasim.Model("]
        INDENT = "    "
        for field in self.fields():
            attr = getattr(self, field)
            if isinstance(attr, EdgeTable):
                content.append(f"{INDENT}{field}=Edge(...),")
            else:
                if isinstance(attr, MultiNodeModel) and attr.node.df is None:
                    # Skip unused node types
                    continue
                content.append(f"{INDENT}{field}={repr(attr)},")

        content.append(")")
        return "\n".join(content)

    def _write_toml(self, fn: FilePath):
        fn = Path(fn)

        content = self.model_dump(exclude_unset=True, exclude_none=True, by_alias=True)
        # Filter empty dicts (default Nodes)
        content = dict(filter(lambda x: x[1], content.items()))
        content["ribasim_version"] = ribasim.__version__
        with open(fn, "wb") as f:
            tomli_w.dump(content, f)
        return fn

    def _save(self, directory: DirectoryPath, input_dir: DirectoryPath):
        db_path = directory / input_dir / "database.gpkg"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.unlink(missing_ok=True)
        context_file_loading.get()["database"] = db_path
        self.edge._save(directory, input_dir)
        for sub in self._nodes():
            sub._save(directory, input_dir)

    def node_table(self) -> NodeTable:
        """Compute the full NodeTable from all node types."""
        df_chunks = [node.node.df for node in self._nodes()]
        df = pd.concat(df_chunks, ignore_index=True)  # type: ignore
        node_table = NodeTable(df=df)
        node_table.sort()
        return node_table

    def _nodes(self) -> Generator[MultiNodeModel, Any, None]:
        """Return all non-empty MultiNodeModel instances."""
        for key in self.model_fields.keys():
            attr = getattr(self, key)
            if (
                isinstance(attr, MultiNodeModel)
                and attr.node.df is not None
                # Model.read creates empty node tables (#1278)
                and not attr.node.df.empty
            ):
                yield attr

    def _children(self):
        return {
            k: getattr(self, k)
            for k in self.model_fields.keys()
            if isinstance(getattr(self, k), ChildModel)
        }

    def validate_model_node_field_ids(self):
        raise NotImplementedError()

    def validate_model_node_ids(self):
        raise NotImplementedError()

    def validate_model(self):
        """Validate the model.

        Checks:
        - Whether the node IDs of the node_type fields are valid
        - Whether the node IDs in the node field correspond to the node IDs on the node type fields
        """

        self.validate_model_node_field_ids()
        self.validate_model_node_ids()

    @classmethod
    def read(cls, filepath: FilePath) -> "Model":
        """Read model from TOML file."""
        return cls(filepath=filepath)  # type: ignore

    def write(self, filepath: Path | str) -> Path:
        """
        Write the contents of the model to disk and save it as a TOML configuration file.

        If ``filepath.parent`` does not exist, it is created before writing.

        Parameters
        ----------
        filepath: FilePath ending in .toml
        """
        # TODO
        # self.validate_model()
        filepath = Path(filepath)
        if not filepath.suffix == ".toml":
            raise ValueError(f"Filepath '{filepath}' is not a .toml file.")
        context_file_loading.set({})
        filepath = Path(filepath)
        directory = filepath.parent
        directory.mkdir(parents=True, exist_ok=True)
        self._save(directory, self.input_dir)
        fn = self._write_toml(filepath)

        context_file_loading.set({})
        return fn

    @classmethod
    def _load(cls, filepath: Path | None) -> dict[str, Any]:
        context_file_loading.set({})

        if filepath is not None:
            with open(filepath, "rb") as f:
                config = tomli.load(f)

            directory = filepath.parent / config.get("input_dir", ".")
            context_file_loading.get()["directory"] = directory
            context_file_loading.get()["database"] = directory / "database.gpkg"

            return config
        else:
            return {}

    @model_validator(mode="after")
    def reset_contextvar(self) -> "Model":
        # Drop database info
        context_file_loading.set({})
        return self

    def plot_control_listen(self, ax):
        raise NotImplementedError()

    def plot(self, ax=None, indicate_subnetworks: bool = True) -> Any:
        """
        Plot the nodes, edges and allocation networks of the model.

        Parameters
        ----------
        ax : matplotlib.pyplot.Artist, optional
            Axes on which to draw the plot.

        Returns
        -------
        ax : matplotlib.pyplot.Artist
        """
        if ax is None:
            _, ax = plt.subplots()
            ax.axis("off")

        node = self.node_table()
        self.edge.plot(ax=ax, zorder=2)
        node.plot(ax=ax, zorder=3)
        # TODO
        # self.plot_control_listen(ax)
        # node.plot(ax=ax, zorder=3)

        handles, labels = ax.get_legend_handles_labels()

        # TODO
        # if indicate_subnetworks:
        #     (
        #         handles_subnetworks,
        #         labels_subnetworks,
        #     ) = node.plot_allocation_networks(ax=ax, zorder=1)
        #     handles += handles_subnetworks
        #     labels += labels_subnetworks

        ax.legend(handles, labels, loc="lower left", bbox_to_anchor=(1, 0.5))

        return ax
