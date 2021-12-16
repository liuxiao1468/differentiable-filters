#!/usr/bin/env bash
source conf.sh


if [[ -z $IMAGE_TAG ]]
then
  echo "No docker tag provided. Cannot run docker image."
else
  echo "Warning: Removing containers with the prefix $CONTAINER_NAME* "
  docker rm -f $CONTAINER_NAME

docker run --gpus all \
			-d \
			--env-file conf.sh \
			--name $CONTAINER_NAME \
			-v /data/xiao/diff_filter/Diff_filters:/tf \
			-v /data/datasets:/datasets \
			radiusaiinc/diffkalman:$IMAGE_TAG \
			/bin/bash -c "python3 run_filter_new.py"
fi