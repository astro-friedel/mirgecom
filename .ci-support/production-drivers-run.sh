#!/bin/bash
set -x
# This script is designed to run the CEESD "production" drivers after
# they have been prepared by an external helper script called
# production-drivers-install.sh. The drivers are each expected to be
# in a directory called "production_driver_*" and are expected to have
# a test driver in "production_driver_*/smoke_test/driver.py".
DRIVERS_HOME=${1:-"."}
cd ${DRIVERS_HOME}
DRIVERS_HOME=$(pwd)

export MIRGE_MPI_EXEC="mpiexec"

if [[ $(hostname) == "porter" ]]; then
    rm -rf run_gpus_generic.sh
    cat <<EOF > run_gpus_generic.sh
if [[ -n "\$OMPI_COMM_WORLD_NODE_RANK" ]]; then
    # Open MPI
    export CUDA_VISIBLE_DEVICES=\$OMPI_COMM_WORLD_LOCAL_RANK
elif [[ -n "\$MPI_LOCALRANKID" ]]; then
     # mpich/mvapich
     export CUDA_VISIBLE_DEVICES=\$MPI_LOCALRANKID
fi

"\$@"
EOF
    chmod +x run_gpus_generic.sh
    export MIRGE_PARALLEL_SPAWNER="bash ${DRIVERS_HOME}/run_gpus_generic.sh"

    # Assumes POCL
    export PYOPENCL_TEST="port:nv"
    export PYOPENCL_CTX="port:nv"
elif [[ $(hostname) == "lassen"* ]]; then
    export PYOPENCL_CTX="port:tesla"
    export PYOPENCL_TEST="port:tesla"
    export XDG_CACHE_HOME="/tmp/$USER/xdg-scratch"
    export MIRGE_MPI_EXEC="jsrun -g 1 -a 1"
fi

for production_driver in $(ls | grep "production_driver_");
do
    . ${production_driver}/scripts/smoke_test.sh "${MIRGE_MPI_EXEC}" "${MIRGE_PARALLEL_SPAWN}"
done
cd -
