from concurrent.futures import Executor
from functools import partial
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from tqdm import tqdm

from pyscreener.utils import calc_score
from pyscreener.docking.utils import run_and_parse_docker

DOCK6 = Path(os.environ['DOCK6'])
DOCK6_PARAMS = DOCK6 / 'parameters'
VDW_DEFN_FILE = DOCK6_PARAMS / 'vdw_AMBER_parm99.defn'
FLEX_DEFN_FILE = DOCK6_PARAMS / 'flex.defn'
FLEX_DRIVE_FILE = DOCK6_PARAMS / 'flex_drive.tbl'

DOCK6 = str(DOCK6 / 'bin' / 'dock6')

def dock_inputs(ligands: Tuple[str, List[Tuple[str, str]]],
                receptors: List[Tuple[str, str]],
                path: str = '.', repeats: int = 1,
                score_mode: str = 'best',
                client: Optional[Executor] = None, 
                chunksize: int = 32, **kwargs) -> List[List[List[Dict]]]:
    """Dock the ligands into the ensemble of receptors

    Parameters
    ----------
    ligands : List[Tuple[str, str]]
        A list of tuples corresponding to the ligands that should be docked
    receptors : List[Tuple[str, str]]
    path : Union[str, os.PathLike] (Deafult = '.')
    repeats : int (Default = 1)
    score_mode : str (Default = 'best')
    chunksize : int (Default = 32)
        the chunksize argument passed to map()
    client : Optional[Executor] (Default = None)
        an object that implements the Executor interface. If specified, docking 
        will be parallelized over client workers. Otherwise, docking will be 
        peformed sequentially

    Returns
    -------
    rowsss : List[List[List[Dict]]]
        a list of ensemble docking results for each ligand. The outermost list 
        is ligands, the second outermost list is the receptors each ligand was
        docked against, the innermost list is the repeated docking runs for a
        single ligand/receptor combo, and the dictionary is a singular row of
        a dataframe. See also: dock_ligand
    """
    path = Path(path)
    
    dock_ligand_ = partial(
        dock_ligand, receptors=receptors, path=path,
        score_mode=score_mode, repeats=repeats,
    )

    map_ = client.map if client else map
    rowsss = list(tqdm(
        map_(dock_ligand_, ligands, chunksize=chunksize),
        total=len(ligands), smoothing=0.,
        desc='Docking ligands', unit='ligand'
    ))

    return rowsss

def dock_ligand(ligand: Tuple[str, str], receptors: List[Tuple[str, str]],
                path: Union[str, os.PathLike] = '.', repeats: int = 1,
                score_mode: str = 'best') -> List[List[Dict]]:
    """Dock this ligand into the ensemble of receptors

    Parameters
    ----------
    ligand : Tuple[str, str]
        a tuple containing the ligand's SMILES string and its prepared
        .mol2 file that will be docked against each receptor
    receptors : List[Tuple[str, str]]
        a list of tuples containing the sphere file and grid file prefix
        corresponding to each receptor in the ensemble.
    path : Union[str, os.PathLike] (Deafult = '.')
        the path under which to oragnize all inputs and outputs
    repeats : int (Default = 1)
        the number of times each docking run should be repeated
    score_mode : str (Default = 'best')
        the method used to calculate a ligand's overall score from the scores
        of each of its docked conformers
    
    Returns
    -------
    ensemble_rowss : List[Dataframe]
        a list of dataframes for this ligand's docking runs into the
        ensemble of receptor poses, each containing the following columns:
            smiles  - the ligand's SMILES string
            name    - the name of the docking run
            in      - the filename of the input docking file
            out     - the filename of the output docked ligand file
            log     - the filename of the output log file
            score   - the ligand's docking score
    """
    if repeats <= 0:
        raise ValueError(f'Repeats must be greater than 0! ({repeats})')

    smi, lig_mol2 = ligand

    ensemble_rowss = []
    for sph_file, grid_prefix in receptors:
        repeat_rows = []
        for repeat in range(repeats):
            name = f'{Path(sph_file).stem}_{Path(lig_mol2).stem}_{repeat}'

            infile, outfile_prefix = prepare_input_file(
                lig_mol2, sph_file, grid_prefix, name, path
            )

            log = Path(outfile_prefix).parent / f'{name}.out'
            argv = [DOCK6, '-i', infile, '-o', log]

            out = Path(f'{outfile_prefix}_scored.mol2')
            score = run_and_parse_docker(argv, parse_out, out, score_mode)

            if score:
                repeat_rows.append({
                    'smiles': smi,
                    'name': name,
                    'in': Path(infile.parent.name) / infile.name,
                    'log': Path(log.parent.name) / log.name,
                    'out': Path(out.parent.name) / out.name,
                    'score': score
                })

        if repeat_rows:
            ensemble_rowss.append(repeat_rows)

    return ensemble_rowss

def parse_out(outfile: Union[str, os.PathLike],
              score_mode : str = 'best') -> Optional[float]:
    """Parse the log file generated from a run of Vina-type docking software
    and return the appropriate score.

    Parameters
    ----------
    outfile : Union[str, PathLike]
        the filename of a scored outfile file generated by DOCK6 or a 
        PathLike object pointing to that file
    score_mode : str (Default = 'best')
        The method used to calculate the docking score from the outfile file.
        See also pyscreener.utils.calc_score for more details

    Returns
    -------
    score : Optional[float]
        the parsed score given the input scoring mode or None if the log
        file was unparsable 
    """
    scores = []
    with open(outfile) as fid:
        for line in fid:
            if 'Grid_Score:' in line:
                try:
                    scores.append(float(line.split()[2]))
                except:
                    continue

    return calc_score(scores, score_mode)

def prepare_input_file(ligand_file: str, sph_file: str, grid_prefix: str,
                       name: Optional[str] = None,
                       path: str = '.') -> Tuple[str, str]:
    """Prepare the input file with which to run DOCK

    Parameters
    ----------
    ligand_file : str
        the input .mol2 corresponding to the ligand that will be docked
    sph_file : str
        the .sph file containing the DOCK spheres of the receptor
    grid_prefix : str
        the prefix of the prepared grid files (as was passed to 
        the grid program)
    name : Optional[str] (Default = None)
        the name to use for the input file and output file
    path : str (Default = '.')
        the root path under which to organize both the input file and output
        file. The input file will be placed under <path>/inputs/ and
        the output file will be placed under <path>/outputs/

    Returns
    -------
    infile: str
        the name of the input file
    outfile_prefix: str
        the prefix of the outfile name. DOCK will automatically name outfiles
        as <outfile_prefix>_scored.mol2
    """
    path = Path(path)
    
    name = name or f'{Path(sph_file).stem}_{Path(ligand_file).stem}'
    infile = path / 'inputs' / f'{name}.in'

    out_dir = path / 'outputs'
    if not out_dir.is_dir():
        out_dir.mkdir(parents=True)
    outfile_prefix = out_dir / name

    with open(infile, 'w') as fid:
        fid.write('conformer_search_type flex\n')
        fid.write('write_fragment_libraries no\n')
        fid.write('user_specified_anchor no\n')
        fid.write('limit_max_anchors no\n')
        fid.write('min_anchor_size 5\n')

        fid.write('pruning_use_clustering yes\n')
        fid.write('pruning_max_orients 100\n')
        fid.write('pruning_clustering_cutoff 100\n')
        fid.write('pruning_conformer_score_cutoff 100.0\n')
        fid.write('pruning_conformer_score_scaling_factor 1.0\n')

        fid.write('use_clash_overlap no\n')
        fid.write('write_growth_tree no\n')
        fid.write('use_internal_energy yes\n')
        fid.write('internal_energy_rep_exp 12\n')
        fid.write('internal_energy_cutoff 100.0\n')

        fid.write(f'ligand_atom_file {ligand_file}\n')
        fid.write('limit_max_ligands no\n')
        fid.write('skip_molecule no\n')
        fid.write('read_mol_solvation no\n')
        fid.write('calculate_rmsd no\n')
        fid.write('use_rmsd_reference_mol no\n')
        fid.write('use_database_filter no\n')
        fid.write('orient_ligand yes\n')
        fid.write('automated_matching yes\n')
        fid.write(f'receptor_site_file {sph_file}\n')
        fid.write('max_orientations 1000\n')
        fid.write('critical_points no\n')
        fid.write('chemical_matching no\n')
        fid.write('use_ligand_spheres no\n')
        fid.write('bump_filter no\n')
        fid.write('score_molecules yes\n')

        fid.write('contact_score_primary no\n')
        fid.write('contact_score_secondary no\n')

        fid.write('grid_score_primary yes\n')
        fid.write('grid_score_secondary no\n')
        fid.write('grid_score_rep_rad_scale 1\n')
        fid.write('grid_score_vdw_scale 1\n')
        fid.write('grid_score_es_scale 1\n')
        fid.write(f'grid_score_grid_prefix {grid_prefix}\n')

        fid.write('multigrid_score_secondary no\n')
        fid.write('dock3.5_score_secondary no\n')
        fid.write('continuous_score_secondary no\n')
        fid.write('footprint_similarity_score_secondary no\n')
        fid.write('pharmacophore_score_secondary no\n')
        fid.write('descriptor_score_secondary no\n')
        fid.write('gbsa_zou_score_secondary no\n')
        fid.write('gbsa_hawkins_score_secondary no\n')
        fid.write('SASA_score_secondary no\n')
        fid.write('amber_score_secondary no\n')

        fid.write('minimize_ligand yes\n')
        fid.write('minimize_anchor yes\n')
        fid.write('minimize_flexible_growth yes\n')
        fid.write('use_advanced_simplex_parameters no\n')

        fid.write('simplex_max_cycles 1\n')
        fid.write('simplex_score_converge 0.1\n')
        fid.write('simplex_cycle_converge 1.0\n')
        fid.write('simplex_trans_step 1.0\n')
        fid.write('simplex_rot_step 0.1\n')
        fid.write('simplex_tors_step 10.0\n')
        fid.write('simplex_anchor_max_iterations 500\n')
        fid.write('simplex_grow_max_iterations 500\n')
        fid.write('simplex_grow_tors_premin_iterations 0\n')
        fid.write('simplex_random_seed 0\n')
        fid.write('simplex_restraint_min no\n')

        fid.write('atom_model all\n')
        fid.write(f'vdw_defn_file {VDW_DEFN_FILE}\n')
        fid.write(f'flex_defn_file {FLEX_DEFN_FILE}\n')
        fid.write(f'flex_drive_file {FLEX_DRIVE_FILE}\n')

        fid.write(f'ligand_outfile_prefix {outfile_prefix}\n')
        fid.write('write_orientations no\n')
        fid.write('num_scored_conformers 5\n')
        fid.write('write_conformations no\n')
        fid.write('rank_ligands no\n')
    
    return infile, outfile_prefix