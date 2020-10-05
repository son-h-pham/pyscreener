"""This module contains functions for running vina-type docking software"""
from concurrent.futures import Executor
from datetime import date
from functools import partial
from itertools import takewhile
from os import PathLike
from math import exp
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from tqdm import tqdm

from pyscreener.utils import calc_score
from pyscreener.docking.utils import Ligand, run_and_parse_docker

Dataframe = List[Dict]

def dock_inputs(docker: str, receptors: List[str], ligands: List[Ligand],
                center: Tuple[float, float, float],
                size: Tuple[int, int, int] = (10, 10, 10),
                ncpu: int = 4, extra: Optional[List[str]] = None,
                path: str = '.', score_mode: str = 'best', repeats: int = 1,
                client: Optional[Executor] = None, 
                chunksize: int = 32) -> List[List[List[Dict]]]:
    """Run the specified docking program with the input ligands and parameters

    Parameters
    ----------
    docker : str
        the vina-type docking program to use
    receptors : List[str]
        the files containing receptor poses against which each ligand 
        should be docked
    ligands : List[Tuple[string, string]]
        a list of tuples containing the SMILES string and the input filename
        for all ligands to be screened
    center : Tuple[float, float, float]
        the x-, y-, and z-coordinates, respectively, of the search box center
    size : Tuple[int, int, int] (Default = (10, 10, 10))
        the x, y, and z-radii, respectively, of the search box
    ncpu : int (Default = 1)
        the number of cores to allocate to the docking program
    path : string (Default = '.')
        the path under which both the log and out files should be written to
    score_mode : str (Default = 'best')
        the method used to calculate the docking score of an individual
        docking run
    repeats : int (Default = 1)
        the number of times a docking run should be repeated
    chunksize : int (Default = 32)
        the chunksize argument passed to map()
    client : Optional[Executor] (Default = None)
        an object that implements the Executor interface. If specified, docking 
        will be parallelized over client workers. Otherwise, docking will be 
        peformed sequentially

    Returns
    -------
    List[List[List[Dict]]]
        a list of ensemble docking results for each ligand. The outermost list 
        is ligands, the second outermost list is the receptors each ligand was
        docked against, the innermost list is the repeated docking runs for a
        single ligand/receptor combo, and the dictionary is a singular row of
        a dataframe. See also: dock_ligand
    """
    path = Path(path) / 'outputs'

    dock_ligand_ = partial(
        dock_ligand, docker=docker, receptors=receptors,
        center=center, size=size, ncpu=ncpu, extra=extra,
        path=path, score_mode=score_mode, repeats=repeats
    )

    map_ = client.map if client else map
    return [
        ensemble_results for ensemble_results in tqdm(
            map_(dock_ligand_, ligands, chunksize=chunksize),
            desc='Docking ligands', unit='ligand',
            total=len(ligands), smoothing=0.,
        )
    ]

def dock_ligand(ligand: Ligand, docker: str, receptors: List[str],
                center: Tuple[float, float, float],
                size: Tuple[int, int, int] = (10, 10, 10), ncpu: int = 1, 
                path: str = '.', extra: Optional[List[str]] = None,
                repeats: int = 1, score_mode: str = 'best') -> List[List[Dict]]:
    """Dock the given ligand using the specified vina-type docking program and 
    parameters into the ensemble of receptors repeatedly
    
    Parameters
    ----------
    docker : str
        the docking program to run
    ligand : Ligand
        a tuple containing a ligand's SMILES string and associated docking
        input file
    receptors : List[str]
        the filesnames of PDBQT files corresponding to various receptor poses
    center : Tuple[float, float, float]
        the x-, y-, and z-coordinates, respectively, of the search box center
    size : Tuple[int, int, int] (Default = (10, 10, 10))
        the x, y, and z-radii, respectively, of the search box
    path : string (Default = '.')
        the path under which both the log and out files should be written to
    ncpu : int (Default = 1)
        the number of cores to allocate to the docking program
    score_mode : str (Default = 'best')
        the method used to calculate the docking score of an individual
        docking run
    repeats : int (Default = 1)
        the number of times to repeat a docking run

    Return
    ------
    ensemble_rowss : List[Dataframe]
        a list of dataframes for this ligand's docking runs into the
        ensemble of receptor poses, each containing the following columns:
            smiles  - the ligand's SMILES string
            name    - the name of the ligand
            in      - the filename of the input ligand file
            out     - the filename of the output docked ligand file
            log     - the filename of the output log file
            score   - the ligand's docking score
    """
    if repeats <= 0:
        raise ValueError(f'Repeats must be greater than 0! ({repeats})')

    smi, pdbqt = ligand

    p_pdbqt = Path(pdbqt)
    ligand_name = p_pdbqt.stem

    ensemble_rowss = []
    for receptor in receptors:
        repeat_rows = []
        for repeat in range(repeats):
            name = f'{Path(receptor).stem}_{ligand_name}_{repeat}'

            argv, p_out, p_log = build_argv(
                docker=docker, receptor=receptor, ligand=pdbqt, name=name,
                center=center, size=size, ncpu=ncpu, extra=extra, path=path
            )

            score = run_and_parse_docker(argv, parse_log, p_log, score_mode)

            if score:
                repeat_rows.append({
                    'smiles': smi,
                    'name': ligand_name,
                    'in': Path(p_pdbqt.parent.name) / p_pdbqt.name,
                    'out': Path(p_out.parent.name) / p_out.name,
                    'log': Path(p_log.parent.name) / p_log.name,
                    'score': score
                })

        if repeat_rows:
            ensemble_rowss.append(repeat_rows)

    return ensemble_rowss
    
def build_argv(docker: str, receptor: str, ligand: str,
               center: Tuple[float, float, float],
               size: Tuple[int, int, int] = (10, 10, 10),
               ncpu: int = 1, name: Optional[str] = None, path: str = '.',
               extra = Optional[List[str]]) -> Tuple[List[str], str, str]:
    """Builds the argument vector to run a vina-type docking program

    Parameters
    ----------
    docker : str
        the name of the docking program to run
    receptor : str
        the filename of the input receptor file
    ligand : str
        the filename of the input ligand file
    center : Tuple[float, float, float]
        the coordinates (x,y,z) of the center of the vina search box
    size : Tuple[int, int, int] (Default = (10, 10, 10))
        the size of the vina search box in angstroms for the x, y, and z-
        dimensions, respectively
    ncpu : int (Default = 1)
        the number of cores to allocate to the docking program
    name : string (Default = <receptor>_<ligand>)
        the base name to use for both the log and out files
    path : string (Default = '.')
        the path under which both the log and out files should be written
    extra : Optional[List[str]]
        additional command line arguments to pass to each run

    Returns
    -------
    argv : List[str]
        the argument vector with which to run an instance of a vina-type
        docking program
    out : str
        the filepath of the out file which the docking program will write to
    log : str
        the filepath of the log file which the docking program will write to
    """
    if docker not in {'vina', 'smina', 'psovina', 'qvina'}:
        raise ValueError(f'Invalid docking program: "{docker}"')

    path = Path(path)
    if not path.is_dir():
        path.mkdir(parents=True)

    name = name or (Path(receptor).stem+'_'+Path(ligand).stem)
    extra = extra or []

    out = path / f'{docker}_{name}_out.pdbqt'
    log = path / f'{docker}_{name}_log.txt'
    
    argv = [
        docker, f'--receptor={receptor}', f'--ligand={ligand}',
        f'--center_x={center[0]}',
        f'--center_y={center[1]}',
        f'--center_z={center[2]}',
        f'--size_x={size[0]}', f'--size_y={size[1]}', f'--size_z={size[2]}',
        f'--cpu={ncpu}', f'--out={out}', f'--log={log}', *extra
    ]

    return argv, out, log

def parse_log(log: Union[str, PathLike],
              score_mode : str = 'best') -> Optional[float]:
    """Parse the log file generated from a run of Vina-type docking software
    and return the appropriate score.

    Parameters
    ----------
    log : Union[str, PathLike]
        the filename of a log file generated by vina-type docking program or a
        PathLike object pointing to that file
    score_mode : str (Default = 'best')
        The method used to calculate the docking score from the log file.
        See also pyscreener.utils.calc_score for more details

    Returns
    -------
    score : Optional[float]
        the parsed score given the input scoring mode or None if the log
        file was unparsable 
    """
    # vina-type log files have scoring information between this table border
    # and the line containing "Writing output ... done."
    TABLE_BORDER = '-----+------------+----------+----------'

    with open(log) as fid:
        for line in fid:
            if TABLE_BORDER in line:
                break

        score_lines = takewhile(lambda line: 'Writing' not in line, fid)
        scores = [float(line.split()[1]) for line in score_lines]

    if len(scores) == 0:
        return None

    return calc_score(scores, score_mode)
