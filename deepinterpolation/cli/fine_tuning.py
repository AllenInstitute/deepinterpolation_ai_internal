import json
import logging
import sys
from pathlib import Path
from typing import Dict, Optional, List, Type

import argschema
import h5py
import numpy as np

from deepinterpolation.cli.schemas import FineTuningInputSchema
from deepinterpolation.generator_collection import MovieJSONGenerator
from deepinterpolation.generic import ClassLoader
from deepinterpolation.trainor_collection import transfer_trainer


class FineTuning(argschema.ArgSchemaParser):
    def __init__(
            self,
            input_data: Dict,
            args: Optional[List] = None):
        super().__init__(
            input_data=input_data,
            args=args,
            schema_type=FineTuningInputSchema)
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        logging.basicConfig(
            format='%(asctime)s %(name)s %(levelname)-8s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            level=self.logger.level,
            stream=sys.stdout
        )
        logger = logging.getLogger(type(self).__name__)
        logger.setLevel(level=self.logger.level)
        self.logger = logger

    def run(self):
        uid = self.args["run_uid"]

        outdir = Path(self.args["finetuning_params"]["output_dir"])
        if self.args["output_full_args"]:
            full_args_path = outdir / f"{uid}_training_full_args.json"
            with open(full_args_path, "w") as f:
                json.dump(self.args, f, indent=2)
            self.logger.info(f"wrote {full_args_path}")

        # We create the output model filename if empty
        if self.args["finetuning_params"]["model_string"] == "":
            self.args["finetuning_params"]["model_string"] = self.args[
                "finetuning_params"
            ]["loss"]

        # TODO: The following lines will be remove once we deprecate the legacy
        # parameter tracking system

        # We pass on the uid
        self.args["finetuning_params"]["run_uid"] = uid

        # We convert to old schema
        self.args["finetuning_params"]["nb_gpus"] = 2 * int(
            self.args["finetuning_params"]["multi_gpus"]
        )

        # To be removed once fully transitioned to CLI
        self.args["generator_params"]["train_path"] = self.args["generator_params"][
            "data_path"
        ]
        self.args["test_generator_params"]["train_path"] = self.args[
            "test_generator_params"
        ]["data_path"]

        # Forward parameters to the training agent
        self.args["finetuning_params"]["steps_per_epoch"] = self.args[
            "finetuning_params"
        ]["steps_per_epoch"]

        self.args["finetuning_params"]["batch_size"] = self.args["generator_params"][
            "batch_size"
        ]

        # This is used to send to the legacy parameter tracking system
        # to specify each sub-object type.
        self.args["generator_params"]["type"] = "generator"
        self.args["test_generator_params"]["type"] = "generator"
        self.args["finetuning_params"]["type"] = "trainer"

        # save the json parameters to 2 different files
        finetuning_json_path = outdir / f"{uid}_finetuning.json"
        with open(finetuning_json_path, "w") as f:
            json.dump(self.args["finetuning_params"], f, indent=2)
        self.logger.info(f"wrote {finetuning_json_path}")

        generator_json_path = outdir / f"{uid}_generator.json"
        with open(generator_json_path, "w") as f:
            json.dump(self.args["generator_params"], f, indent=2)
        self.logger.info(f"wrote {generator_json_path}")

        test_generator_json_path = outdir / f"{uid}_test_generator.json"
        with open(test_generator_json_path, "w") as f:
            json.dump(self.args["test_generator_params"], f, indent=2)
        self.logger.info(f"wrote {test_generator_json_path}")

        movs = self._maybe_cache_data(
            train_generator_json_path=generator_json_path,
            test_generator_json_path=test_generator_json_path
        )

        generator_obj: Type[MovieJSONGenerator] = \
            ClassLoader(generator_json_path).find_and_build()
        data_generator = generator_obj(
            json_path=generator_json_path,
            movs=movs
        )

        test_generator_obj: Type[MovieJSONGenerator] = \
            ClassLoader(test_generator_json_path).find_and_build()
        data_test_generator = test_generator_obj(
            json_path=test_generator_json_path,
            movs=movs
        )

        finetuning_obj = ClassLoader(finetuning_json_path)

        training_class: transfer_trainer = finetuning_obj.find_and_build()(
            trainer_json_path=finetuning_json_path
        )

        self.logger.info("created objects for training")
        training_class.run(
            train_generator=data_generator,
            test_generator=data_test_generator
        )

    def _maybe_cache_data(
        self,
        train_generator_json_path: Path,
        test_generator_json_path: Path
    ) -> Optional[Dict[int, np.ndarray]]:
        """
        Loads movies into memory if `cache_data` is `True`.

        Parameters
        ----------
        train_generator_json_path
        test_generator_json_path

        Returns
        -------
        Map from ophys experiment id to movie, if `cache_data` is
        `True`, otherwise `None`
        """
        if not self.args["finetuning_params"]["cache_data"]:
            return None

        movs = {}
        with open(train_generator_json_path) as f:
            train_generator_config = json.load(f)
        with open(train_generator_config["train_path"]) as f:
            train_data = json.load(f)

        with open(test_generator_json_path) as f:
            test_generator_config = json.load(f)
        with open(test_generator_config["train_path"]) as f:
            test_data = json.load(f)

        train_ophys_experiment_ids = list(train_data.keys())
        test_ophys_experiment_ids = list(test_data.keys())

        if sorted(train_ophys_experiment_ids) != \
                sorted(test_ophys_experiment_ids):
            raise NotImplementedError('Different ophys experiments in '
                                      'train/test not supported when using '
                                      '`cache_data`')
        ophys_experiment_ids = train_ophys_experiment_ids

        if len(ophys_experiment_ids) > 1:
            self.logger.warning(
                'cache_data will use a lot of '
                'memory if there are multiple movies. Are you sure?')
        for experiment_id in ophys_experiment_ids:
            self.logger.info(f'Loading {experiment_id} into memory')
            with h5py.File(
                    train_data[experiment_id]["path"], "r") as f:
                movs[experiment_id] = f['data'][()]
        return movs


if __name__ == "__main__":  # pragma: nocover
    fine = FineTuning()
    fine.run()
