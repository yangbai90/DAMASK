import os
import copy
import warnings
import multiprocessing as mp
from functools import partial
import typing
from typing import Union, Optional, TextIO, List, Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import h5py
from scipy import ndimage, spatial

from . import VTK
from . import util
from . import grid_filters
from . import Rotation
from . import Table
from ._typehints import FloatSequence, IntSequence

class Grid:
    """
    Geometry definition for grid solvers.

    Create and manipulate geometry definitions for storage as VTK
    image data files ('.vti' extension). A grid contains the
    material ID (referring to the entry in 'material.yaml') and
    the physical size.
    """

    def __init__(self,
                 material: np.ndarray,
                 size: FloatSequence,
                 origin: FloatSequence = np.zeros(3),
                 comments: Union[str, Sequence[str]] = None):
        """
        New geometry definition for grid solvers.

        Parameters
        ----------
        material : numpy.ndarray, shape (:,:,:)
            Material indices. The shape of the material array defines
            the number of cells.
        size : sequence of float, len (3)
            Physical size of grid in meter.
        origin : sequence of float, len (3), optional
            Coordinates of grid origin in meter. Defaults to [0.0,0.0,0.0].
        comments : (list of) str, optional
            Comments, e.g. history of operations.

        """
        self.material = material
        self.size = size                                                                            # type: ignore
        self.origin = origin                                                                        # type: ignore
        self.comments = [] if comments is None else comments                                        # type: ignore


    def __repr__(self) -> str:
        """Basic information on grid definition."""
        mat_min = np.nanmin(self.material)
        mat_max = np.nanmax(self.material)
        mat_N   = self.N_materials
        return util.srepr([
               f'cells:  {util.srepr(self.cells, " × ")}',
               f'size:   {util.srepr(self.size,  " × ")} m³',
               f'origin: {util.srepr(self.origin,"   ")} m',
               f'# materials: {mat_N}' + ('' if mat_min == 0 and mat_max+1 == mat_N else
                                          f' (min: {mat_min}, max: {mat_max})')
              ])


    def __copy__(self) -> 'Grid':
        """Create deep copy."""
        return copy.deepcopy(self)

    copy = __copy__


    def __eq__(self,
               other: object) -> bool:
        """
        Test equality of other.

        Parameters
        ----------
        other : damask.Grid
            Grid to compare self against.

        """
        if not isinstance(other, Grid):
            return NotImplemented
        return bool(np.allclose(other.size,self.size)
            and np.allclose(other.origin,self.origin)
            and np.all(other.cells == self.cells)
            and np.all(other.material == self.material))


    @property
    def material(self) -> np.ndarray:
        """Material indices."""
        return self._material

    @material.setter
    def material(self,
                 material: np.ndarray):
        if len(material.shape) != 3:
            raise ValueError(f'invalid material shape {material.shape}')
        if material.dtype not in np.sctypes['float'] and material.dtype not in np.sctypes['int']:
            raise TypeError(f'invalid material data type "{material.dtype}"')

        self._material = np.copy(material)

        if self.material.dtype in np.sctypes['float'] and \
           np.all(self.material == self.material.astype(int).astype(float)):
            self._material = self.material.astype(int)


    @property
    def size(self) -> np.ndarray:
        """Physical size of grid in meter."""
        return self._size

    @size.setter
    def size(self,
             size: FloatSequence):
        if len(size) != 3 or any(np.array(size) < 0):
            raise ValueError(f'invalid size {size}')

        self._size = np.array(size)

    @property
    def origin(self) -> np.ndarray:
        """Coordinates of grid origin in meter."""
        return self._origin

    @origin.setter
    def origin(self,
               origin: FloatSequence):
        if len(origin) != 3:
            raise ValueError(f'invalid origin {origin}')

        self._origin = np.array(origin)

    @property
    def comments(self) -> List[str]:
        """Comments, e.g. history of operations."""
        return self._comments

    @comments.setter
    def comments(self,
                 comments: Union[str, Sequence[str]]):
        self._comments = [str(c) for c in comments] if isinstance(comments,list) else [str(comments)]


    @property
    def cells(self) -> np.ndarray:
        """Number of cells in x,y,z direction."""
        return np.asarray(self.material.shape)


    @property
    def N_materials(self) -> int:
        """Number of (unique) material indices within grid."""
        return np.unique(self.material).size


    @staticmethod
    def load(fname: Union[str, Path]) -> 'Grid':
        """
        Load from VTK image data file.

        Parameters
        ----------
        fname : str or pathlib.Path
            Grid file to read. Valid extension is .vti, which will be appended
            if not given.

        Returns
        -------
        loaded : damask.Grid
            Grid-based geometry from file.

        """
        v = VTK.load(fname if str(fname).endswith('.vti') else str(fname)+'.vti')
        comments = v.get_comments()
        cells = np.array(v.vtk_data.GetDimensions())-1
        bbox  = np.array(v.vtk_data.GetBounds()).reshape(3,2).T

        return Grid(material = v.get('material').reshape(cells,order='F'),
                    size = bbox[1] - bbox[0],
                    origin = bbox[0],
                    comments=comments)


    @typing. no_type_check
    @staticmethod
    def load_ASCII(fname)-> 'Grid':
        """
        Load from geom file.

        Storing geometry files in ASCII format is deprecated.
        This function will be removed in a future version of DAMASK.

        Parameters
        ----------
        fname : str, pathlib.Path, or file handle
            Geometry file to read.

        Returns
        -------
        loaded : damask.Grid
            Grid-based geometry from file.

        """
        warnings.warn('Support for ASCII-based geom format will be removed in DAMASK 3.0.0', DeprecationWarning,2)
        if isinstance(fname, (str, Path)):
            f = open(fname)
        elif isinstance(fname, TextIO):
            f = fname
        else:
            raise TypeError

        f.seek(0)
        try:
            header_length_,keyword = f.readline().split()[:2]
            header_length = int(header_length_)
        except ValueError:
            header_length,keyword = (-1, 'invalid')
        if not keyword.startswith('head') or header_length < 3:
            raise TypeError('invalid or missing header length information')

        comments = []
        content = f.readlines()
        for i,line in enumerate(content[:header_length]):
            items = line.split('#')[0].lower().strip().split()
            if (key := items[0] if items else '') ==  'grid':
                cells  = np.array([  int(dict(zip(items[1::2],items[2::2]))[i]) for i in ['a','b','c']])
            elif key == 'size':
                size   = np.array([float(dict(zip(items[1::2],items[2::2]))[i]) for i in ['x','y','z']])
            elif key == 'origin':
                origin = np.array([float(dict(zip(items[1::2],items[2::2]))[i]) for i in ['x','y','z']])
            else:
                comments.append(line.strip())

        material = np.empty(int(cells.prod()))                                                      # initialize as flat array
        i = 0
        for line in content[header_length:]:
            if len(items := line.split('#')[0].split()) == 3:
                if items[1].lower() == 'of':
                    material_entry = np.ones(int(items[0]))*float(items[2])
                elif items[1].lower() == 'to':
                    material_entry = np.linspace(int(items[0]),int(items[2]),
                                        abs(int(items[2])-int(items[0]))+1,dtype=float)
                else:                        material_entry = list(map(float, items))
            else:                            material_entry = list(map(float, items))
            material[i:i+len(material_entry)] = material_entry
            i += len(items)

        if i != cells.prod():
            raise TypeError(f'mismatch between {cells.prod()} expected entries and {i} found')

        if not np.any(np.mod(material,1) != 0.0):                                                   # no float present
            material = material.astype('int') - (1 if material.min() > 0 else 0)

        return Grid(material.reshape(cells,order='F'),size,origin,comments)


    @staticmethod
    def load_Neper(fname: Union[str, Path]) -> 'Grid':
        """
        Load from Neper VTK file.

        Parameters
        ----------
        fname : str or pathlib.Path
            Geometry file to read.

        Returns
        -------
        loaded : damask.Grid
            Grid-based geometry from file.

        """
        v = VTK.load(fname,'ImageData')
        cells = np.array(v.vtk_data.GetDimensions())-1
        bbox  = np.array(v.vtk_data.GetBounds()).reshape(3,2).T

        return Grid(v.get('MaterialId').reshape(cells,order='F').astype('int32',casting='unsafe') - 1,
                    bbox[1] - bbox[0], bbox[0],
                    util.execution_stamp('Grid','load_Neper'))


    @staticmethod
    def load_DREAM3D(fname: Union[str, Path],
                     feature_IDs: str = None, cell_data: str = None,
                     phases: str = 'Phases', Euler_angles: str = 'EulerAngles',
                     base_group: str = None) -> 'Grid':
        """
        Load DREAM.3D (HDF5) file.

        Data in DREAM.3D files can be stored per cell ('CellData') and/or
        per grain ('Grain Data'). Per default, cell-wise data is assumed.

        damask.ConfigMaterial.load_DREAM3D gives the corresponding material definition.

        Parameters
        ----------
        fname : str or or pathlib.Path
            Filename of the DREAM.3D (HDF5) file.
        feature_IDs : str, optional
            Name of the dataset containing the mapping between cells and
            grain-wise data. Defaults to 'None', in which case cell-wise
            data is used.
        cell_data : str, optional
            Name of the group (folder) containing cell-wise data. Defaults to
            None in wich case it is automatically detected.
        phases : str, optional
            Name of the dataset containing the phase ID. It is not used for
            grain-wise data, i.e. when feature_IDs is not None.
            Defaults to 'Phases'.
        Euler_angles : str, optional
            Name of the dataset containing the crystallographic orientation as
            Euler angles in radians It is not used for grain-wise data, i.e.
            when feature_IDs is not None. Defaults to 'EulerAngles'.
        base_group : str, optional
            Path to the group (folder) that contains geometry (_SIMPL_GEOMETRY),
            and grain- or cell-wise data. Defaults to None, in which case
            it is set as the path that contains _SIMPL_GEOMETRY/SPACING.

        Returns
        -------
        loaded : damask.Grid
            Grid-based geometry from file.

        """
        b = util.DREAM3D_base_group(fname)      if base_group is None else base_group
        c = util.DREAM3D_cell_data_group(fname) if cell_data  is None else cell_data
        f = h5py.File(fname, 'r')

        cells  = f['/'.join([b,'_SIMPL_GEOMETRY','DIMENSIONS'])][()]
        size   = f['/'.join([b,'_SIMPL_GEOMETRY','SPACING'])] * cells
        origin = f['/'.join([b,'_SIMPL_GEOMETRY','ORIGIN'])][()]

        if feature_IDs is None:
            phase = f['/'.join([b,c,phases])][()].reshape(-1,1)
            O = Rotation.from_Euler_angles(f['/'.join([b,c,Euler_angles])]).as_quaternion().reshape(-1,4) # noqa
            unique,unique_inverse = np.unique(np.hstack([O,phase]),return_inverse=True,axis=0)
            ma = np.arange(cells.prod()) if len(unique) == cells.prod() else \
                 np.arange(unique.size)[np.argsort(pd.unique(unique_inverse))][unique_inverse]
        else:
            ma = f['/'.join([b,c,feature_IDs])][()].flatten()

        return Grid(ma.reshape(cells,order='F'),size,origin,util.execution_stamp('Grid','load_DREAM3D'))


    @staticmethod
    def from_table(table: Table,
                   coordinates: str,
                   labels: Union[str, Sequence[str]]) -> 'Grid':
        """
        Create grid from ASCII table.

        Parameters
        ----------
        table : damask.Table
            Table that contains material information.
        coordinates : str
            Label of the vector column containing the spatial coordinates.
            Need to be ordered (1./x fast, 3./z slow).
        labels : (list of) str
            Label(s) of the columns containing the material definition.
            Each unique combination of values results in one material ID.

        Returns
        -------
        new : damask.Grid
            Grid-based geometry from values in table.

        """
        cells,size,origin = grid_filters.cellsSizeOrigin_coordinates0_point(table.get(coordinates))

        labels_ = [labels] if isinstance(labels,str) else labels
        unique,unique_inverse = np.unique(np.hstack([table.get(l) for l in labels_]),return_inverse=True,axis=0)

        ma = np.arange(cells.prod()) if len(unique) == cells.prod() else \
             np.arange(unique.size)[np.argsort(pd.unique(unique_inverse))][unique_inverse]

        return Grid(ma.reshape(cells,order='F'),size,origin,util.execution_stamp('Grid','from_table'))


    @staticmethod
    def _find_closest_seed(seeds: np.ndarray,
                           weights: np.ndarray,
                           point: np.ndarray) -> np.integer:
        return np.argmin(np.sum((np.broadcast_to(point,(len(seeds),3))-seeds)**2,axis=1) - weights)

    @staticmethod
    def from_Laguerre_tessellation(cells: IntSequence,
                                   size: FloatSequence,
                                   seeds: np.ndarray,
                                   weights: FloatSequence,
                                   material: IntSequence = None,
                                   periodic: bool = True):
        """
        Create grid from Laguerre tessellation.

        Parameters
        ----------
        cells : sequence of int, len (3)
            Number of cells in x,y,z direction.
        size : sequence of float, len (3)
            Physical size of the grid in meter.
        seeds : numpy.ndarray, shape (:,3)
            Position of the seed points in meter. All points need to lay within the box.
        weights : sequence of float, len (seeds.shape[0])
            Weights of the seeds. Setting all weights to 1.0 gives a standard Voronoi tessellation.
        material : sequence of int, len (seeds.shape[0]), optional
            Material ID of the seeds.
            Defaults to None, in which case materials are consecutively numbered.
        periodic : bool, optional
            Assume grid to be periodic. Defaults to True.

        Returns
        -------
        new : damask.Grid
            Grid-based geometry from tessellation.

        """
        weights_p: FloatSequence
        if periodic:
            weights_p = np.tile(weights,27)                                                         # Laguerre weights (1,2,3,1,2,3,...,1,2,3)
            seeds_p = np.vstack((seeds  -np.array([size[0],0.,0.]),seeds,  seeds  +np.array([size[0],0.,0.])))
            seeds_p = np.vstack((seeds_p-np.array([0.,size[1],0.]),seeds_p,seeds_p+np.array([0.,size[1],0.])))
            seeds_p = np.vstack((seeds_p-np.array([0.,0.,size[2]]),seeds_p,seeds_p+np.array([0.,0.,size[2]])))
        else:
            weights_p = np.array(weights,float)
            seeds_p   = seeds

        coords = grid_filters.coordinates0_point(cells,size).reshape(-1,3)

        pool = mp.Pool(int(os.environ.get('OMP_NUM_THREADS',4)))
        result = pool.map_async(partial(Grid._find_closest_seed,seeds_p,weights_p), coords)
        pool.close()
        pool.join()
        material_ = np.array(result.get()).reshape(cells)

        if periodic: material_ %= len(weights)

        return Grid(material = material_ if material is None else np.array(material)[material_],
                    size     = size,
                    comments = util.execution_stamp('Grid','from_Laguerre_tessellation'),
                   )


    @staticmethod
    def from_Voronoi_tessellation(cells: IntSequence,
                                  size: FloatSequence,
                                  seeds: np.ndarray,
                                  material: IntSequence = None,
                                  periodic: bool = True) -> 'Grid':
        """
        Create grid from Voronoi tessellation.

        Parameters
        ----------
        cells : sequence of int, len (3)
            Number of cells in x,y,z direction.
        size : sequence of float, len (3)
            Physical size of the grid in meter.
        seeds : numpy.ndarray, shape (:,3)
            Position of the seed points in meter. All points need to lay within the box.
        material : sequence of int, len (seeds.shape[0]), optional
            Material ID of the seeds.
            Defaults to None, in which case materials are consecutively numbered.
        periodic : bool, optional
            Assume grid to be periodic. Defaults to True.

        Returns
        -------
        new : damask.Grid
            Grid-based geometry from tessellation.

        """
        coords = grid_filters.coordinates0_point(cells,size).reshape(-1,3)
        tree = spatial.cKDTree(seeds,boxsize=size) if periodic else \
               spatial.cKDTree(seeds)
        try:
            material_ = tree.query(coords, workers = int(os.environ.get('OMP_NUM_THREADS',4)))[1]
        except TypeError:
            material_ = tree.query(coords, n_jobs = int(os.environ.get('OMP_NUM_THREADS',4)))[1]    # scipy <1.6

        return Grid(material = (material_ if material is None else np.array(material)[material_]).reshape(cells),
                    size     = size,
                    comments = util.execution_stamp('Grid','from_Voronoi_tessellation'),
                   )


    _minimal_surface = \
        {'Schwarz P':        lambda x,y,z: np.cos(x) + np.cos(y) + np.cos(z),
         'Double Primitive': lambda x,y,z: ( 0.5 * (np.cos(x)*np.cos(y) + np.cos(y)*np.cos(z) + np.cos(z)*np.cos(x))
                                           + 0.2 * (np.cos(2*x) + np.cos(2*y) + np.cos(2*z)) ),
         'Schwarz D':        lambda x,y,z: ( np.sin(x)*np.sin(y)*np.sin(z)
                                           + np.sin(x)*np.cos(y)*np.cos(z)
                                           + np.cos(x)*np.cos(y)*np.sin(z)
                                           + np.cos(x)*np.sin(y)*np.cos(z) ),
         'Complementary D':  lambda x,y,z: ( np.cos(3*x+y)*np.cos(z) - np.sin(3*x-y)*np.sin(z) + np.cos(x+3*y)*np.cos(z)
                                           + np.sin(x-3*y)*np.sin(z) + np.cos(x-y)*np.cos(3*z) - np.sin(x+y)*np.sin(3*z) ),
         'Double Diamond':   lambda x,y,z: 0.5 * (np.sin(x)*np.sin(y)
                                                + np.sin(y)*np.sin(z)
                                                + np.sin(z)*np.sin(x)
                                                + np.cos(x) * np.cos(y) * np.cos(z) ),
         'Dprime':           lambda x,y,z: 0.5 * ( np.cos(x)*np.cos(y)*np.cos(z)
                                                 + np.cos(x)*np.sin(y)*np.sin(z)
                                                 + np.sin(x)*np.cos(y)*np.sin(z)
                                                 + np.sin(x)*np.sin(y)*np.cos(z)
                                                 - np.sin(2*x)*np.sin(2*y)
                                                 - np.sin(2*y)*np.sin(2*z)
                                                 - np.sin(2*z)*np.sin(2*x) ) - 0.2,
         'Gyroid':           lambda x,y,z: np.cos(x)*np.sin(y) + np.cos(y)*np.sin(z) + np.cos(z)*np.sin(x),
         'Gprime':           lambda x,y,z : ( np.sin(2*x)*np.cos(y)*np.sin(z)
                                            + np.sin(2*y)*np.cos(z)*np.sin(x)
                                            + np.sin(2*z)*np.cos(x)*np.sin(y) ) + 0.32,
         'Karcher K':        lambda x,y,z: ( 0.3 * ( np.cos(x)           + np.cos(y)           + np.cos(z)
                                                   + np.cos(x)*np.cos(y) + np.cos(y)*np.cos(z) + np.cos(z)*np.cos(x) )
                                           - 0.4 * ( np.cos(2*x)         + np.cos(2*y)         + np.cos(2*z) ) ) + 0.2,
         'Lidinoid':         lambda x,y,z: 0.5 * ( np.sin(2*x)*np.cos(y)*np.sin(z)
                                                 + np.sin(2*y)*np.cos(z)*np.sin(x)
                                                 + np.sin(2*z)*np.cos(x)*np.sin(y)
                                                 - np.cos(2*x)*np.cos(2*y)
                                                 - np.cos(2*y)*np.cos(2*z)
                                                 - np.cos(2*z)*np.cos(2*x) ) + 0.15,
         'Neovius':          lambda x,y,z: ( 3 * (np.cos(x)+np.cos(y)+np.cos(z))
                                           + 4 * np.cos(x)*np.cos(y)*np.cos(z) ),
         'Fisher-Koch S':    lambda x,y,z: ( np.cos(2*x)*np.sin(  y)*np.cos(  z)
                                           + np.cos(  x)*np.cos(2*y)*np.sin(  z)
                                           + np.sin(  x)*np.cos(  y)*np.cos(2*z) ),
      }


    @staticmethod
    def from_minimal_surface(cells: IntSequence,
                             size: FloatSequence,
                             surface: str,
                             threshold: float = 0.0,
                             periods: int = 1,
                             materials: IntSequence = (0,1)) -> 'Grid':
        """
        Create grid from definition of triply periodic minimal surface.

        Parameters
        ----------
        cells : sequence of int, len (3)
            Number of cells in x,y,z direction.
        size : sequence of float, len (3)
            Physical size of the grid in meter.
        surface : str
            Type of the minimal surface. See notes for details.
        threshold : float, optional.
            Threshold of the minimal surface. Defaults to 0.0.
        periods : integer, optional.
            Number of periods per unit cell. Defaults to 1.
        materials : sequence of int, len (2)
            Material IDs. Defaults to (0,1).

        Returns
        -------
        new : damask.Grid
            Grid-based geometry from definition of minimal surface.

        Notes
        -----
        The following triply-periodic minimal surfaces are implemented:
          - Schwarz P
          - Double Primitive
          - Schwarz D
          - Complementary D
          - Double Diamond
          - Dprime
          - Gyroid
          - Gprime
          - Karcher K
          - Lidinoid
          - Neovius
          - Fisher-Koch S

        References
        ----------
        S.B.G. Blanquer et al., Biofabrication 9(2):025001, 2017
        https://doi.org/10.1088/1758-5090/aa6553

        M. Wohlgemuth et al., Macromolecules 34(17):6083-6089, 2001
        https://doi.org/10.1021/ma0019499

        M.-T. Hsieh and L. Valdevit, Software Impacts 6:100026, 2020
        https://doi.org/10.1016/j.simpa.2020.100026

        Examples
        --------
        Minimal surface of 'Gyroid' type.

        >>> import numpy as np
        >>> import damask
        >>> damask.Grid.from_minimal_surface([64]*3,np.ones(3)*1.e-4,'Gyroid')
        cells : 64 x 64 x 64
        size  : 0.0001 x 0.0001 x 0.0001 m³
        origin: 0.0   0.0   0.0 m
        # materials: 2

        Minimal surface of 'Neovius' type. non-default material IDs.

        >>> import numpy as np
        >>> import damask
        >>> damask.Grid.from_minimal_surface([80]*3,np.ones(3)*5.e-4,
        ...                                  'Neovius',materials=(1,5))
        cells : 80 x 80 x 80
        size  : 0.0005 x 0.0005 x 0.0005 m³
        origin: 0.0   0.0   0.0 m
        # materials: 2 (min: 1, max: 5)

        """
        x,y,z = np.meshgrid(periods*2.0*np.pi*(np.arange(cells[0])+0.5)/cells[0],
                            periods*2.0*np.pi*(np.arange(cells[1])+0.5)/cells[1],
                            periods*2.0*np.pi*(np.arange(cells[2])+0.5)/cells[2],
                            indexing='ij',sparse=True)
        return Grid(material = np.where(threshold < Grid._minimal_surface[surface](x,y,z),materials[1],materials[0]),
                    size     = size,
                    comments = util.execution_stamp('Grid','from_minimal_surface'),
                   )


    def save(self,
             fname: Union[str, Path],
             compress: bool = True):
        """
        Save as VTK image data file.

        Parameters
        ----------
        fname : str or pathlib.Path
            Filename to write. Valid extension is .vti, it will be appended if not given.
        compress : bool, optional
            Compress with zlib algorithm. Defaults to True.

        """
        v = VTK.from_image_data(self.cells,self.size,self.origin)
        v.add(self.material.flatten(order='F'),'material')
        v.add_comments(self.comments)

        v.save(fname,parallel=False,compress=compress)


    def save_ASCII(self,
                   fname: Union[str, TextIO]):
        """
        Save as geom file.

        Storing geometry files in ASCII format is deprecated.
        This function will be removed in a future version of DAMASK.

        Parameters
        ----------
        fname : str or file handle
            Geometry file to write with extension '.geom'.
        compress : bool, optional
            Compress geometry with 'x of y' and 'a to b'.

        """
        warnings.warn('Support for ASCII-based geom format will be removed in DAMASK 3.0.0', DeprecationWarning,2)
        header =  [f'{len(self.comments)+4} header'] + self.comments \
                + ['grid   a {} b {} c {}'.format(*self.cells),
                   'size   x {} y {} z {}'.format(*self.size),
                   'origin x {} y {} z {}'.format(*self.origin),
                   'homogenization 1',
                  ]

        format_string = '%g' if self.material.dtype in np.sctypes['float'] else \
                        '%{}i'.format(1+int(np.floor(np.log10(np.nanmax(self.material)))))
        np.savetxt(fname,
                   self.material.reshape([self.cells[0],np.prod(self.cells[1:])],order='F').T,
                   header='\n'.join(header), fmt=format_string, comments='')


    def show(self) -> None:
        """Show on screen."""
        VTK.from_image_data(self.cells,self.size,self.origin).show()


    def add_primitive(self,
                      dimension: Union[FloatSequence, IntSequence],
                      center: Union[FloatSequence, IntSequence],
                      exponent: Union[FloatSequence, float],
                      fill: int = None,
                      R: Rotation = Rotation(),
                      inverse: bool = False,
                      periodic: bool = True) -> 'Grid':
        """
        Insert a primitive geometric object at a given position.

        Parameters
        ----------
        dimension : sequence of int or float, len (3)
            Dimension (diameter/side length) of the primitive.
            If given as integers, cell centers are addressed.
            If given as floats, physical coordinates are addressed.
        center : sequence of int or float, len (3)
            Center of the primitive.
            If given as integers, cell centers are addressed.
            If given as floats, physical coordinates are addressed.
        exponent : float or sequence of float, len (3)
            Exponents for the three axes.
            0 gives octahedron (ǀxǀ^(2^0) + ǀyǀ^(2^0) + ǀzǀ^(2^0) < 1)
            1 gives sphere     (ǀxǀ^(2^1) + ǀyǀ^(2^1) + ǀzǀ^(2^1) < 1)
        fill : int, optional
            Fill value for primitive. Defaults to material.max()+1.
        R : damask.Rotation, optional
            Rotation of primitive. Defaults to no rotation.
        inverse : bool, optional
            Retain original materials within primitive and fill outside.
            Defaults to False.
        periodic : bool, optional
            Assume grid to be periodic. Defaults to True.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        Examples
        --------
        Add a sphere at the center.

        >>> import numpy as np
        >>> import damask
        >>> g = damask.Grid(np.zeros([64]*3,int), np.ones(3)*1e-4)
        >>> g.add_primitive(np.ones(3)*5e-5,np.ones(3)*5e-5,1)
        cells : 64 x 64 x 64
        size  : 0.0001 x 0.0001 x 0.0001 m³
        origin: 0.0   0.0   0.0 m
        # materials: 2

        Add a cube at the origin.

        >>> import numpy as np
        >>> import damask
        >>> g = damask.Grid(np.zeros([64]*3,int), np.ones(3)*1e-4)
        >>> g.add_primitive(np.ones(3,int)*32,np.zeros(3),np.inf)
        cells : 64 x 64 x 64
        size  : 0.0001 x 0.0001 x 0.0001 m³
        origin: 0.0   0.0   0.0 m
        # materials: 2

        """
        # radius and center
        r = np.array(dimension)/2.0*self.size/self.cells if np.array(dimension).dtype in np.sctypes['int'] else \
            np.array(dimension)/2.0
        c = (np.array(center) + .5)*self.size/self.cells if np.array(center).dtype    in np.sctypes['int'] else \
            (np.array(center) - self.origin)

        coords = grid_filters.coordinates0_point(self.cells,self.size,
                                          -(0.5*(self.size + (self.size/self.cells
                                                              if np.array(center).dtype in np.sctypes['int'] else
                                                              0)) if periodic else c))
        coords_rot = R.broadcast_to(tuple(self.cells))@coords

        with np.errstate(all='ignore'):
            mask = np.sum(np.power(coords_rot/r,2.0**np.array(exponent)),axis=-1) > 1.0

        if periodic:                                                                                # translate back to center
            mask = np.roll(mask,((c/self.size-0.5)*self.cells).round().astype(int),(0,1,2))

        return Grid(material = np.where(np.logical_not(mask) if inverse else mask,
                                        self.material,
                                        np.nanmax(self.material)+1 if fill is None else fill),
                    size     = self.size,
                    origin   = self.origin,
                    comments = self.comments+[util.execution_stamp('Grid','add_primitive')],
                   )


    def mirror(self,
               directions: Sequence[str],
               reflect: bool = False) -> 'Grid':
        """
        Mirror grid along given directions.

        Parameters
        ----------
        directions : (sequence of) {'x', 'y', 'z'}
            Direction(s) along which the grid is mirrored.
        reflect : bool, optional
            Reflect (include) outermost layers. Defaults to False.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        Examples
        --------
        Mirror along x- and y-direction.

        >>> import numpy as np
        >>> import damask
        >>> g = damask.Grid(np.zeros([32]*3,int), np.ones(3)*1e-4)
        >>> g.mirror('xy',True)
        cells : 64 x 64 x 32
        size  : 0.0002 x 0.0002 x 0.0001 m³
        origin: 0.0   0.0   0.0 m
        # materials: 1

        """
        if not set(directions).issubset(valid := ['x', 'y', 'z']):
            raise ValueError(f'invalid direction "{set(directions).difference(valid)}" specified')

        limits: Sequence[Optional[int]] = [None,None] if reflect else [-2,0]
        mat = self.material.copy()

        if 'x' in directions:
            mat = np.concatenate([mat,mat[limits[0]:limits[1]:-1,:,:]],0)
        if 'y' in directions:
            mat = np.concatenate([mat,mat[:,limits[0]:limits[1]:-1,:]],1)
        if 'z' in directions:
            mat = np.concatenate([mat,mat[:,:,limits[0]:limits[1]:-1]],2)

        return Grid(material = mat,
                    size     = self.size/self.cells*np.asarray(mat.shape),
                    origin   = self.origin,
                    comments = self.comments+[util.execution_stamp('Grid','mirror')],
                   )


    def flip(self,
             directions: Sequence[str]) -> 'Grid':
        """
        Flip grid along given directions.

        Parameters
        ----------
        directions : (sequence of) {'x', 'y', 'z'}
            Direction(s) along which the grid is flipped.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        """
        if not set(directions).issubset(valid := ['x', 'y', 'z']):
            raise ValueError(f'invalid direction "{set(directions).difference(valid)}" specified')


        mat = np.flip(self.material, [valid.index(d) for d in directions if d in valid])

        return Grid(material = mat,
                    size     = self.size,
                    origin   = self.origin,
                    comments = self.comments+[util.execution_stamp('Grid','flip')],
                   )


    def scale(self,
              cells: IntSequence,
              periodic: bool = True) -> 'Grid':
        """
        Scale grid to new cells.

        Parameters
        ----------
        cells : sequence of int, len (3)
            Number of cells in x,y,z direction.
        periodic : bool, optional
            Assume grid to be periodic. Defaults to True.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        Examples
        --------
        Double resolution.

        >>> import numpy as np
        >>> import damask
        >>> g = damask.Grid(np.zeros([32]*3,int),np.ones(3)*1e-4)
        >>> g.scale(g.cells*2)
        cells : 64 x 64 x 64
        size  : 0.0001 x 0.0001 x 0.0001 m³
        origin: 0.0   0.0   0.0 m
        # materials: 1

        """
        return Grid(material = ndimage.interpolation.zoom(
                                                           self.material,
                                                           cells/self.cells,
                                                           output=self.material.dtype,
                                                           order=0,
                                                           mode=('wrap' if periodic else 'nearest'),
                                                           prefilter=False
                                                          ),
                    size     = self.size,
                    origin   = self.origin,
                    comments = self.comments+[util.execution_stamp('Grid','scale')],
                   )


    def clean(self,
              stencil: int = 3,
              selection: IntSequence = None,
              periodic: bool = True) -> 'Grid':
        """
        Smooth grid by selecting most frequent material index within given stencil at each location.

        Parameters
        ----------
        stencil : int, optional
            Size of smoothing stencil.
        selection : sequence of int, optional
            Field values that can be altered. Defaults to all.
        periodic : bool, optional
            Assume grid to be periodic. Defaults to True.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        """
        def mostFrequent(arr: np.ndarray, selection = None):
            me = arr[arr.size//2]
            if selection is None or me in selection:
                unique, inverse = np.unique(arr, return_inverse=True)
                return unique[np.argmax(np.bincount(inverse))]
            else:
                return me

        return Grid(material = ndimage.filters.generic_filter(
                                                               self.material,
                                                               mostFrequent,
                                                               size=(stencil if selection is None else stencil//2*2+1,)*3,
                                                               mode=('wrap' if periodic else 'nearest'),
                                                               extra_keywords=dict(selection=selection),
                                                              ).astype(self.material.dtype),
                    size     = self.size,
                    origin   = self.origin,
                    comments = self.comments+[util.execution_stamp('Grid','clean')],
                   )


    def renumber(self) -> 'Grid':
        """
        Renumber sorted material indices as 0,...,N-1.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        """
        _,renumbered = np.unique(self.material,return_inverse=True)

        return Grid(material = renumbered.reshape(self.cells),
                    size     = self.size,
                    origin   = self.origin,
                    comments = self.comments+[util.execution_stamp('Grid','renumber')],
                   )


    def rotate(self,
               R: Rotation,
               fill: int = None) -> 'Grid':
        """
        Rotate grid (pad if required).

        Parameters
        ----------
        R : damask.Rotation
            Rotation to apply to the grid.
        fill : int, optional
            Material index to fill the corners. Defaults to material.max() + 1.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        """
        material = self.material
        # These rotations are always applied in the reference coordinate system, i.e. (z,x,z) not (z,x',z'')
        # see https://www.cs.utexas.edu/~theshark/courses/cs354/lectures/cs354-14.pdf
        for angle,axes in zip(R.as_Euler_angles(degrees=True)[::-1], [(0,1),(1,2),(0,1)]):
            material_temp = ndimage.rotate(material,angle,axes,order=0,prefilter=False,
                                           output=self.material.dtype,
                                           cval=np.nanmax(self.material) + 1 if fill is None else fill)
            # avoid scipy interpolation errors for rotations close to multiples of 90°
            material = material_temp if np.prod(material_temp.shape) != np.prod(material.shape) else \
                       np.rot90(material,k=np.rint(angle/90.).astype(int),axes=axes)

        origin = self.origin-(np.asarray(material.shape)-self.cells)*.5 * self.size/self.cells

        return Grid(material = material,
                    size     = self.size/self.cells*np.asarray(material.shape),
                    origin   = origin,
                    comments = self.comments+[util.execution_stamp('Grid','rotate')],
                   )


    def canvas(self,
               cells: IntSequence = None,
               offset: IntSequence = None,
               fill: int = None) -> 'Grid':
        """
        Crop or enlarge/pad grid.

        Parameters
        ----------
        cells : sequence of int, len (3), optional
            Number of cells  x,y,z direction.
        offset : sequence of int, len (3), optional
            Offset (measured in cells) from old to new grid [0,0,0].
        fill : int, optional
            Material index to fill the background. Defaults to material.max() + 1.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        Examples
        --------
        Remove 1/2 of the microstructure in z-direction.

        >>> import numpy as np
        >>> import damask
        >>> g = damask.Grid(np.zeros([32]*3,int),np.ones(3)*1e-4)
        >>> g.canvas([32,32,16])
        cells : 33 x 32 x 16
        size  : 0.0001 x 0.0001 x 5e-05 m³
        origin: 0.0   0.0   0.0 m
        # materials: 1

        """
        offset_ = np.array(offset,int) if offset is not None else np.zeros(3,int)
        cells_ = np.array(cells,int) if cells is not None else self.cells

        canvas = np.full(cells_,np.nanmax(self.material) + 1 if fill is None else fill,self.material.dtype)

        LL = np.clip( offset_,           0,np.minimum(self.cells,     cells_+offset_))
        UR = np.clip( offset_+cells_,    0,np.minimum(self.cells,     cells_+offset_))
        ll = np.clip(-offset_,           0,np.minimum(     cells_,self.cells-offset_))
        ur = np.clip(-offset_+self.cells,0,np.minimum(     cells_,self.cells-offset_))

        canvas[ll[0]:ur[0],ll[1]:ur[1],ll[2]:ur[2]] = self.material[LL[0]:UR[0],LL[1]:UR[1],LL[2]:UR[2]]

        return Grid(material = canvas,
                    size     = self.size/self.cells*np.asarray(canvas.shape),
                    origin   = self.origin+offset_*self.size/self.cells,
                    comments = self.comments+[util.execution_stamp('Grid','canvas')],
                   )


    def substitute(self,
                   from_material: IntSequence,
                   to_material: IntSequence) -> 'Grid':
        """
        Substitute material indices.

        Parameters
        ----------
        from_material : sequence of int
            Material indices to be substituted.
        to_material : sequence of int
            New material indices.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        """
        material = self.material.copy()
        for f,t in zip(from_material,to_material):        # ToDo Python 3.10 has strict mode for zip
            material[self.material==f] = t

        return Grid(material = material,
                    size     = self.size,
                    origin   = self.origin,
                    comments = self.comments+[util.execution_stamp('Grid','substitute')],
                   )


    def sort(self) -> 'Grid':
        """
        Sort material indices such that min(material) is located at (0,0,0).

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        """
        a = self.material.flatten(order='F')
        from_ma = pd.unique(a)
        sort_idx = np.argsort(from_ma)
        ma = np.unique(a)[sort_idx][np.searchsorted(from_ma,a,sorter = sort_idx)]

        return Grid(material = ma.reshape(self.cells,order='F'),
                    size     = self.size,
                    origin   = self.origin,
                    comments = self.comments+[util.execution_stamp('Grid','sort')],
                   )


    def vicinity_offset(self,
                        vicinity: int = 1,
                        offset: int = None,
                        trigger: IntSequence = [],
                        periodic: bool = True) -> 'Grid':
        """
        Offset material index of points in the vicinity of xxx.

        Different from themselves (or listed as triggers) within a given (cubic) vicinity,
        i.e. within the region close to a grain/phase boundary.
        ToDo: use include/exclude as in seeds.from_grid

        Parameters
        ----------
        vicinity : int, optional
            Voxel distance checked for presence of other materials.
            Defaults to 1.
        offset : int, optional
            Offset (positive or negative) to tag material indices,
            defaults to material.max()+1.
        trigger : sequence of int, optional
            List of material indices that trigger a change.
            Defaults to [], meaning that any different neighbor triggers a change.
        periodic : bool, optional
            Assume grid to be periodic. Defaults to True.

        Returns
        -------
        updated : damask.Grid
            Updated grid-based geometry.

        """
        def tainted_neighborhood(stencil: np.ndarray, trigger):
            me = stencil[stencil.shape[0]//2]
            return np.any(stencil != me if len(trigger) == 0 else
                          np.in1d(stencil,np.array(list(set(trigger) - {me}))))

        offset_ = np.nanmax(self.material)+1 if offset is None else offset
        mask = ndimage.filters.generic_filter(self.material,
                                              tainted_neighborhood,
                                              size=1+2*vicinity,
                                              mode='wrap' if periodic else 'nearest',
                                              extra_keywords={'trigger':trigger})

        return Grid(material = np.where(mask, self.material + offset_,self.material),
                    size     = self.size,
                    origin   = self.origin,
                    comments = self.comments+[util.execution_stamp('Grid','vicinity_offset')],
                   )


    def get_grain_boundaries(self,
                             periodic: bool = True,
                             directions: Sequence[str] = 'xyz') -> VTK:
        """
        Create VTK unstructured grid containing grain boundaries.

        Parameters
        ----------
        periodic : bool, optional
            Assume grid to be periodic. Defaults to True.
        directions : (sequence of) {'x', 'y', 'z'}, optional
            Direction(s) along which the boundaries are determined.
            Defaults to 'xyz'.

        Returns
        -------
        grain_boundaries : damask.VTK
            VTK-based geometry of grain boundary network.

        """
        if not set(directions).issubset(valid := ['x', 'y', 'z']):
            raise ValueError(f'invalid direction "{set(directions).difference(valid)}" specified')

        o = [[0, self.cells[0]+1,           np.prod(self.cells[:2]+1)+self.cells[0]+1, np.prod(self.cells[:2]+1)],
             [0, np.prod(self.cells[:2]+1), np.prod(self.cells[:2]+1)+1,               1],
             [0, 1,                         self.cells[0]+1+1,                         self.cells[0]+1]] # offset for connectivity

        connectivity = []
        for i,d in enumerate(['x','y','z']):
            if d not in directions: continue
            mask = self.material != np.roll(self.material,1,i)
            for j in [0,1,2]:
                mask = np.concatenate((mask,np.take(mask,[0],j)*(i==j)),j)
            if i == 0 and not periodic: mask[0,:,:] = mask[-1,:,:] = False
            if i == 1 and not periodic: mask[:,0,:] = mask[:,-1,:] = False
            if i == 2 and not periodic: mask[:,:,0] = mask[:,:,-1] = False

            base_nodes = np.argwhere(mask.flatten(order='F')).reshape(-1,1)
            connectivity.append(np.block([base_nodes + o[i][k] for k in range(4)]))

        coords = grid_filters.coordinates0_node(self.cells,self.size,self.origin).reshape(-1,3,order='F')
        return VTK.from_unstructured_grid(coords,np.vstack(connectivity),'QUAD')
