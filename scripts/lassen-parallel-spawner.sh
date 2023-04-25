#!/bin/bash
#
# Used to wrap the spawning of parallel mirgecom drivers on Lassen
# unset CUDA_CACHE_DISABLE
POCL_CACHE_ROOT=${POCL_CACHE_ROOT:-"/tmp/$USER/pocl-scratch"}
XDG_CACHE_ROOT=${XDG_CACHE_HOME:-"/tmp/$USER/xdg-scratch"}
export POCL_CACHE_DIR="${POCL_CACHE_ROOT}/rank$OMPI_COMM_WORLD_RANK"
export XDG_CACHE_HOME="${XDG_CACHE_ROOT}/rank$OMPI_COMM_WORLD_RANK"

"$@"
