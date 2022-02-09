!--------------------------------------------------------------------------------------------------
!> @author Martin Diehl, Max-Planck-Institut für Eisenforschung GmbH
!> @brief Inquires variables related to parallelization (openMP, MPI)
!--------------------------------------------------------------------------------------------------
module parallelization
  use, intrinsic :: ISO_fortran_env, only: &
    OUTPUT_UNIT

#ifdef PETSC
#include <petsc/finclude/petscsys.h>
  use PETScSys
#if (PETSC_VERSION_MAJOR==3 && PETSC_VERSION_MINOR>14) && !defined(PETSC_HAVE_MPI_F90MODULE_VISIBILITY)
  use MPI_f08
#endif
!$ use OMP_LIB
#endif

  use prec

  implicit none
  private

#ifndef PETSC
  integer, parameter, public :: &
    MPI_INTEGER_KIND = pI64
  integer(MPI_INTEGER_KIND), parameter, public :: &
    worldrank = 0_MPI_INTEGER_KIND, &                                                               !< MPI dummy worldrank
    worldsize = 1_MPI_INTEGER_KIND                                                                  !< MPI dummy worldsize
#else
  integer(MPI_INTEGER_KIND), protected, public :: &
    worldrank = 0_MPI_INTEGER_KIND, &                                                               !< MPI worldrank (/=0 for MPI simulations only)
    worldsize = 1_MPI_INTEGER_KIND                                                                  !< MPI worldsize (/=1 for MPI simulations only)
#endif

#ifndef PETSC
public :: parallelization_bcast_str

contains
subroutine parallelization_bcast_str(string)
  character(len=:), allocatable, intent(inout) :: string
end subroutine parallelization_bcast_str

#else
  public :: &
    parallelization_init, &
    parallelization_bcast_str

contains

!--------------------------------------------------------------------------------------------------
!> @brief Initialize shared memory (openMP) and distributed memory (MPI) parallelization.
!--------------------------------------------------------------------------------------------------
subroutine parallelization_init

  integer(MPI_INTEGER_KIND) :: err_MPI, typeSize
  character(len=4) :: rank_str
!$ integer :: got_env, threadLevel
!$ integer(pI32) :: OMP_NUM_THREADS
!$ character(len=6) NumThreadsString


  PetscErrorCode :: err_PETSc
#ifdef _OPENMP
  ! If openMP is enabled, check if the MPI libary supports it and initialize accordingly.
  ! Otherwise, the first call to PETSc will do the initialization.
  call MPI_Init_Thread(MPI_THREAD_FUNNELED,threadLevel,err_MPI)
  if (err_MPI /= 0_MPI_INTEGER_KIND)   error stop 'MPI init failed'
  if (threadLevel<MPI_THREAD_FUNNELED) error stop 'MPI library does not support OpenMP'
#endif

#if defined(DEBUG)
  call PetscInitialize(PETSC_NULL_CHARACTER,err_PETSc)
#else
  call PetscInitializeNoArguments(err_PETSc)
#endif
  CHKERRQ(err_PETSc)

#if defined(DEBUG) && defined(__INTEL_COMPILER)
  call PetscSetFPTrap(PETSC_FP_TRAP_ON,err_PETSc)
#else
  call PetscSetFPTrap(PETSC_FP_TRAP_OFF,err_PETSc)
#endif
  CHKERRQ(err_PETSc)

  call MPI_Comm_rank(MPI_COMM_WORLD,worldrank,err_MPI)
  if (err_MPI /= 0_MPI_INTEGER_KIND) &
    error stop 'Could not determine worldrank'

  if (worldrank == 0) print'(/,1x,a)', '<<<+-  parallelization init  -+>>>'

  call MPI_Comm_size(MPI_COMM_WORLD,worldsize,err_MPI)
  if (err_MPI /= 0_MPI_INTEGER_KIND) &
    error stop 'Could not determine worldsize'
  if (worldrank == 0) print'(/,1x,a,i3)', 'MPI processes: ',worldsize

  call MPI_Type_size(MPI_INTEGER,typeSize,err_MPI)
  if (err_MPI /= 0_MPI_INTEGER_KIND) &
    error stop 'Could not determine size of MPI_INTEGER'
  if (typeSize*8_MPI_INTEGER_KIND /= int(bit_size(0),MPI_INTEGER_KIND)) &
    error stop 'Mismatch between MPI_INTEGER and DAMASK default integer'

  call MPI_Type_size(MPI_INTEGER8,typeSize,err_MPI)
  if (err_MPI /= 0) &
    error stop 'Could not determine size of MPI_INTEGER8'
  if (typeSize*8_MPI_INTEGER_KIND /= int(bit_size(0_pI64),MPI_INTEGER_KIND)) &
    error stop 'Mismatch between MPI_INTEGER8 and DAMASK pI64'

  call MPI_Type_size(MPI_DOUBLE,typeSize,err_MPI)
  if (err_MPI /= 0_MPI_INTEGER_KIND) &
    error stop 'Could not determine size of MPI_DOUBLE'
  if (typeSize*8_MPI_INTEGER_KIND /= int(storage_size(0.0_pReal),MPI_INTEGER_KIND)) &
    error stop 'Mismatch between MPI_DOUBLE and DAMASK pReal'

  if (worldrank /= 0) then
    close(OUTPUT_UNIT)                                                                              ! disable output
    write(rank_str,'(i4.4)') worldrank                                                              ! use for MPI debug filenames
    open(OUTPUT_UNIT,file='/dev/null',status='replace')                                             ! close() alone will leave some temp files in cwd
  endif

!$ call get_environment_variable(name='OMP_NUM_THREADS',value=NumThreadsString,STATUS=got_env)
!$ if(got_env /= 0) then
!$   print'(1x,a)', 'Could not get $OMP_NUM_THREADS, using default'
!$   OMP_NUM_THREADS = 4_pI32
!$ else
!$   read(NumThreadsString,'(i6)') OMP_NUM_THREADS
!$   if (OMP_NUM_THREADS < 1_pI32) then
!$     print'(1x,a)', 'Invalid OMP_NUM_THREADS: "'//trim(NumThreadsString)//'", using default'
!$     OMP_NUM_THREADS = 4_pI32
!$   endif
!$ endif
!$ print'(1x,a,1x,i2)',   'OMP_NUM_THREADS:',OMP_NUM_THREADS
!$ call omp_set_num_threads(OMP_NUM_THREADS)

end subroutine parallelization_init


!--------------------------------------------------------------------------------------------------
!> @brief Broadcast a string from process 0.
!--------------------------------------------------------------------------------------------------
subroutine parallelization_bcast_str(string)

  character(len=:), allocatable, intent(inout) :: string

  integer(MPI_INTEGER_KIND) :: strlen, err_MPI


  if (worldrank == 0) strlen = len(string,MPI_INTEGER_KIND)
  call MPI_Bcast(strlen,1_MPI_INTEGER_KIND,MPI_INTEGER,0_MPI_INTEGER_KIND,MPI_COMM_WORLD, err_MPI)
  if (worldrank /= 0) allocate(character(len=strlen)::string)

  call MPI_Bcast(string,strlen,MPI_CHARACTER,0_MPI_INTEGER_KIND,MPI_COMM_WORLD, err_MPI)


end subroutine parallelization_bcast_str

#endif

end module parallelization
