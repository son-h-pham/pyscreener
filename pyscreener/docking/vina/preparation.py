"""This module contains functions for preparing input files of vina-type
docking software"""

from pathlib import Path
import subprocess as sp
import sys
from typing import Dict, Iterable, List, Optional, Tuple

from pyscreener.docking.preparation import prepare_receptors, prepare_ligands

def prepare_inputs(receptors: Iterable[str], ligands: Iterable,
                   center: Tuple, size: Tuple[int, int, int] = (20, 20, 20), 
                   ncpu: int = 1, path: str = '.', **kwargs) -> Dict:
    """Prepare the inputs dictionary to pass to pyscreener.docking.dock

    Parameters
    ----------
    receptors : Iterable[str]
        the receptor files to prepare for vina-type docking software
    ligands : Iterable
        the list of files or SMILES strings containing or corresponding to the
        molecules that are to be docked against the given receptors
    center : Tuple[float, float, float]
        the center of the docking box
    size : Tuple[int, int, int], (Default = (20, 20, 20))
        the x-, y-, and z-radii of the docking box
    ncpu : int (Default = 1)
        the number of CPU cores to allocate to each vina run
    path : str (Default = '.')
        the path under which all inputs be written to
    **kwargs
        keyword arguments passed the appropriate ligand preparation function
        (E.g., pyscreener.docking.preparation.prepare_from_[smis,csv,supply])

    Returns
    -------
    Dict
        a dictionary containing the keyword arguments used in either
        pyscreener.docking.dock or pyscreener.docking.vina.dock_inputs
    
    See also
    --------
    pyscreener.docking.preparation.prepare_receptors
    pyscreener.docking.preparation.prepare_ligands

    for documentation of the arguments in **kwargs:
        pyscreener.docking.preparation.prepare_from_smis
        pyscreener.docking.preparation.prepare_from_csv
        pyscreener.docking.preparation.prepare_from_supply

    for documentation of how the output dictionary is utilized:
        pyscreener.docking.docking.dock
        pyscreener.docking.ucsfdock.dock_inputs
        pyscreener.docking.ucsfdock.dock_ligand
    """
    receptors = prepare_receptors(receptors, prepare_receptor)
    ligands = prepare_ligands(ligands, prepare_from_smi, prepare_from_file, 
                              path=f'{path}/inputs', **kwargs)
                              
    return {'receptors': receptors, 'ligands': ligands, 
            'center': center, 'size': size, 'ncpu': ncpu}

def prepare_receptor(receptor: str) -> Optional[str]:
    """Prepare a receptor PDBQT file from its input file

    Parameter
    ---------
    receptor : str
        the filename of a file containing a receptor

    Returns
    -------
    receptor_pdbqt : Optional[str]
        the filename of the resulting PDBQT file. None if preparation failed
    """
    receptor_pdbqt = str(Path(receptor).with_suffix('.pdbqt'))
    args = ['obabel', receptor, '-O', receptor_pdbqt,
            '-xh', '-xr', '--partialcharge', 'gasteiger']
    try:
        sp.run(args, stderr=sp.PIPE, check=True)
    except sp.SubprocessError:
        print(f'ERROR: failed to convert {receptor}', file=sys.stderr)
        return None

    return receptor_pdbqt

def prepare_from_smi(smi: str, name: str = 'ligand',
                     path: str = '.', **kwargs) -> Optional[Tuple]:
    """Prepare an input ligand file from the ligand's SMILES string

    Parameters
    ----------
    smi : str
        the SMILES string of the ligand
    name : Optional[str] (Default = None)
        the name of the ligand.
    path : str (Default = '.')
        the path under which the output PDBQT file should be written
    **kwargs
        additional and unused keyword arguments

    Returns
    -------
    Optional[Tuple]
        a tuple of the SMILES string and the corresponding prepared input file.
        None if preparation failed for any reason
    """
    path = Path(path)
    if not path.is_dir():
        path.mkdir()
    
    pdbqt = str(path / f'{name}.pdbqt')

    argv = ['obabel', f'-:{smi}', '-O', pdbqt,
            '-xh', '--gen3d', '--partialcharge', 'gasteiger']
    ret = sp.run(argv, check=False, stderr=sp.PIPE)

    try:
        ret.check_returncode()
    except sp.SubprocessError:
        return None

    return smi, pdbqt
    
def prepare_from_file(filename: str, use_3d: bool = False,
                      name: Optional[str] = None, path: str = '.', 
                      **kwargs) -> Tuple:
    """Convert a single ligand to the appropriate input format

    Parameters
    ----------
    filename : str
        the name of the file containing the ligand
    use_3d : bool (Default = False)
        whether to use the 3D information in the input file (if possible)
    prepare_from_smi: Callable[..., Tuple[str, str]]
        a function that prepares an input ligand file from a SMILES string
    name : Optional[str] (Default = None)
        the name of the ligand. If None, use the stem of the input file
    path : str (Default = '.')
        the path under which the output .pdbqt file should be written
    **kwargs
        additional and unused keyword arguments

    Returns
    -------
    List[Tuple]
        a tuple of the SMILES string the prepared input file corresponding
        to the molecule contained in filename
    """
    name = name or Path(filename).stem

    ret = sp.run(['obabel', filename, '-osmi'], stdout=sp.PIPE, check=True)
    lines = ret.stdout.decode('utf-8').splitlines()
    smis = [line.split()[0] for line in lines]

    if not use_3d:
        ligands = [prepare_from_smi(smi, f'{name}_{i}', path) 
                   for i, smi in enumerate(smis)]
        return [lig for lig in ligands if lig]
    
    path = Path(path)
    if not path.is_dir():
        path.mkdir()

    pdbqt = f'{path}/{name}_.pdbqt'
    argv = ['obabel', filename, '-opdbqt', '-O', pdbqt, '-m']
    ret = sp.run(argv, check=False, stderr=sp.PIPE)
    
    try:
        ret.check_returncode()
    except sp.SubprocessError:
        return None

    stderr = ret.stderr.decode('utf-8')
    for line in stderr.splitlines():
        if 'converted' not in line:
            continue
        n_mols = int(line.split()[0])

    # have to think about some molecules failing and how that affects numbering
    pdbqts = [f'{path}/{name}_{i}.pdbqt' for i in range(1, n_mols)]

    return list(zip(smis, pdbqts))
    