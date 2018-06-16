from pathlib import Path, PurePath
from typing import List

import re
from collections import namedtuple
from functools import wraps

from runs.database import DataBase
from runs.file_system import FileSystem
from runs.logger import UI
from runs.run_entry import RunEntry
from runs.shell import Bash
from runs.transaction.change_description import DescriptionChange, ChangeDescriptionTransaction
from runs.transaction.interrupt import InterruptTransaction
from runs.transaction.move import Move, MoveTransaction
from runs.transaction.new import NewRunTransaction
from runs.transaction.removal import RemovalTransaction
from runs.transaction.sub_transaction import SubTransaction

TransactionType = namedtuple(
    'TransactionType', [
        'description_change',
        'interrupt',
        'removal',
        'move',
        'new_run',
    ])


def natural_keys(text):
    return [int(c) if c.isdigit() else c for c in re.split('(\d+)', text)]


class Transaction:
    @staticmethod
    def wrapper(func):
        @wraps(func)
        def _wrapper(db_path, root, dir_names, quiet, assume_yes, *args, **kwargs):
            transaction = Transaction(
                db_path=db_path,
                root=root,
                dir_names=dir_names,
                quiet=quiet,
                assume_yes=assume_yes)
            with transaction as open_transaction:
                return func(transaction=open_transaction, *args, **kwargs)

        return _wrapper

    def __init__(self, db_path: Path, quiet: bool, assume_yes: bool, root: Path,
                 dir_names: List[str]):
        self.ui = UI(quiet=quiet, assume_yes=assume_yes)
        self.db = DataBase(path=db_path, logger=self.ui)

        self.bash = Bash(logger=self.ui)
        file_system = FileSystem(root=root, dir_names=dir_names)
        kwargs = dict(
            ui=self.ui,
            db=self.db,
            bash=self.bash,
            file_system=file_system,
        )

        self.sub_transactions = TransactionType(
            description_change=ChangeDescriptionTransaction(**kwargs),
            interrupt=InterruptTransaction(**kwargs),
            removal=RemovalTransaction(**kwargs),
            move=MoveTransaction(**kwargs),
            new_run=NewRunTransaction(**kwargs),
        )

    def __enter__(self):
        self.db = self.db.__enter__()
        return self

    def __exit__(self, *args):
        def sort(st: SubTransaction):
            st.queue = sorted(st.queue,
                              key=lambda x: natural_keys(str(x)))

        def validate(st: SubTransaction):
            st.validate()

        def execute(st: SubTransaction):
            for x in st.queue:
                st.execute(x)

        for process in [sort, validate, execute]:
            for sub_transaction in self.sub_transactions:
                assert isinstance(sub_transaction, SubTransaction)
                if sub_transaction.queue:
                    process(sub_transaction)

        self.db.__exit__(*args)

    def add_run(self, path: PurePath, full_command: str, commit: str, datetime: str,
                description: str, input_command: str):
        self.sub_transactions.new_run.add(
            RunEntry(
                path=path,
                full_command=full_command,
                commit=commit,
                datetime=datetime,
                description=description,
                input_command=input_command))

    def move(self, src: PurePath, dest: PurePath, kill_tmux: bool):
        self.sub_transactions.move.add(Move(src=src, dest=dest, kill_tmux=kill_tmux))

    def remove(self, path: PurePath):
        self.sub_transactions.removal.add(path)

    def interrupt(self, path: PurePath):
        self.sub_transactions.interrupt.add(path)

    def change_description(self, path: PurePath, full_command: str, old_description: str,
                           new_description: str):
        self.sub_transactions.description_change.add(
            DescriptionChange(
                path=path,
                full_command=full_command,
                old_description=old_description,
                new_description=new_description))
