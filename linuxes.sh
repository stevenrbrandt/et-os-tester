set -e
curl -o cactus_new.th -kL https://bitbucket.org/einsteintoolkit/manifest/raw/master/einsteintoolkit.th
M5_NEW=$(md5sum cactus_new.th)
M5=$(md5sum cactus.th)
if [ "$M5" != "$M5_NEW" ]
then
    cp cactus_new.th cactus.th
fi
for os in mint opensuse ubuntu fedora debian rocky alma arch
do
  echo "=============================================="
  echo "TESTING $os"
  #docker system prune -f
  #docker-compose -f ${os}.et.yaml build --pull --no-cache |& tee build.${os}.log
  #set -ex
  docker-compose -f ${os}.et.yaml build |& tee build.${os}.log
  #set +ex
  telegram-send "built ${os}"
  docker-compose -f ${os}.et.yaml down
  docker-compose -f ${os}.et.yaml up -d
  sleep 5
  #rm -f testsuite_results/results/${os}__1_4.log testsuite_results/results/${os}__2_4.log
  set -ex
  docker cp ${os}.et:/home/etuser/${os}__1_4.log testsuite_results/results/
  docker cp ${os}.et:/home/etuser/${os}__2_4.log testsuite_results/results/
  set +ex
  telegram-send "copied ${os}"
  docker-compose -f ${os}.et.yaml down
  echo "FINISHED TESTING $os"
done
