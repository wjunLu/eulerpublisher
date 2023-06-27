# coding=utf-8
import datetime
import docker
import click
import shutil
import wget
import os
import re

ARCHS = ["x86_64", "aarch64"]
PUBLISHER_SUCCESS = 0
PUBLISHER_FAILED = 1

class Publisher:
    repo = None
    version = None
    registry = None
    
    def __init__(self, repo=None, version=None, registry=None):
        self.repo = repo
        self.version = version
        self.registry = registry

    def download(self):
        # 创建以版本号命名的目录
        os.makedirs(self.version, exist_ok=True)
        shutil.copy2('Dockerfile', self.version + '/Dockerfile')
        os.chdir(self.version)

        for arch in ARCHS:
            if arch == 'x86_64':
                docker_arch = 'amd64'
            elif arch == 'aarch64':
                docker_arch = 'arm64'
            else:
                print("Unsupported arch.")
                return PUBLISHER_FAILED

            # 下载文件
            if os.path.exists('openEuler-docker.' + arch + '.tar.xz') == False:
                download_url = "http://repo.openeuler.org/openEuler-" + self.version.upper() + \
                               "/docker_img/" + arch + "/openEuler-docker." + arch + ".tar.xz"
                wget.download(download_url)
                print("\nDownload openEuler-docker." + arch + ".tar.xz successfully.")

            # 校验文件
            os.system('rm -f ' + '/openEuler-docker.' + arch + '.tar.xz.sha256sum')
            sha256sum_url = "http://repo.openeuler.org/openEuler-" + self.version.upper() + \
                "/docker_img/" + arch + "/openEuler-docker." + arch + ".tar.xz.sha256sum"
            wget.download(sha256sum_url)
            print("\nDownload openEuler-docker." + arch + ".tar.xz.sha256sum successfully.")
            os_cmd = 'shasum -c ' + 'openEuler-docker.' + arch + '.tar.xz.sha256sum'
            os.system(os_cmd)

            # 获得rootfs文件
            if os.path.exists('openEuler-docker-rootfs.' + docker_arch + '.tar.xz') == True:
                continue
            os_cmd = 'tar -xf ' + 'openEuler-docker.' + arch + '.tar.xz ' + \
                     '--wildcards "*.tar" --exclude "layer.tar"'
            os.system(os_cmd)
            for file in os.listdir('.'):
                if file.endswith('.tar') and not re.search('openEuler', file):
                    os.system('mv -f ' + file + ' ' + \
                              'openEuler-docker-rootfs.' + docker_arch + '.tar')
                    os.system('xz -z openEuler-docker-rootfs.' + docker_arch + '.tar')

        os.chdir('..')
        return PUBLISHER_SUCCESS

    def build_and_push(self):
        # 检查是否安装qemu,以支持多平台构建
        if os.system('qemu-system-x86_64 --version') != 0:
            print('[ERROR] please install qemu first, you can use command <yum install qemu-img>.')
            return PUBLISHER_FAILED 
        # 创建client
        client = docker.from_env()
        # 登陆仓库
        username = os.environ['LOGIN_USERNAME']
        password = os.environ['LOGIN_PASSWORD']
        client.login(username=username, password=password, registry=self.registry)

        # 考虑到docker API for python版本的差异，直接调用buildx命令实现多平台镜像构建
        builder_name = 'euler_builder_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        if os.system('docker buildx create --use --name ' + builder_name) != 0:
            return PUBLISHER_FAILED 
        # 构建并push docker image
        os.chdir(self.version)
        if os.system('docker buildx build --platform linux/arm64,linux/amd64 ' + \
                     '-t ' + self.repo + ':' + self.version + ' --push .') != 0:
            return PUBLISHER_FAILED
        os.system('docker buildx stop ' + builder_name)
        os.system('docker buildx rm ' +builder_name)

        os.chdir('..')
        return PUBLISHER_SUCCESS
    
    # 运行docker容器，执行command命令，运行结果与预期结果param进行对比，返回成功 0/失败 1
    def run(self, command='', param=''):
        client = docker.from_env()
        container = client.create_container(image=self.repo + ':' + self.version, command=command, detach=True)
        client.start(container)
        os.system('touch logs.txt')
        os.system('docker logs ' + container['Id'] + ' >>logs.txt')
        logs = open(file='logs.txt')
        ret = PUBLISHER_FAILED
        for line in logs:
            if param in line:
                ret = PUBLISHER_SUCCESS
        logs.close()
        os.system('rm -rf logs.txt')
        client.stop(container)
        return ret

    # 对已构建的镜像进行测试，多平台镜像无法保存在本地，需要先从仓库pull后再执行测试过程
    def check(self):
        result = PUBLISHER_SUCCESS
        client = docker.from_env()
        image = client.images(name=self.repo + ':' + self.version)
        if image == []:
            print('Pulling ' + self.repo + ':' + self.version + '...')
            client.pull(self.repo + ':' + self.version)

        # check basic information of new image
        image = client.images(name=self.repo + ':' + self.version)
        image_info = client.inspect_image(image[0]['Id'])
        for tag in image[0]['RepoTags']:
            if tag == self.repo + ':' + self.version:
                # check OS type
                if image_info['Os'] == 'linux':
                    print('[Check Success] OS type <%s> is OK.' % image_info['Os'])
                else:
                    print('[Check Error] OS type <%s> is unknown.' % image_info['Os'])
                    result = PUBLISHER_FAILED
                # check platform type
                if image_info['Architecture'] == 'amd64' or image_info['Architecture'] == 'arm64':
                    print('[Check Success] Architecture <%s> is OK.' % image_info['Architecture'])
                else:
                    print('[Check Error] Architecture <%s> is not expected.' % image_info['Architecture'])
                    result = PUBLISHER_FAILED

        # test time zone settings
        if self.run(command="date", param="UTC") == PUBLISHER_SUCCESS:
            print('[Check Success] time zone setting is OK.')
        else:
            print('[Check Error] time zone setting is not UTC')
            result = PUBLISHER_FAILED
        return result

    # 一键发布
    def publish(self):
        if self.download() != PUBLISHER_SUCCESS:
            print('Download failed.')
            return PUBLISHER_FAILED
        if self.build_and_push() != PUBLISHER_SUCCESS:
            print('Build and push failed.')
            return PUBLISHER_FAILED
        if self.check() != PUBLISHER_SUCCESS:
            return PUBLISHER_FAILED
        return PUBLISHER_SUCCESS

@click.group()
@click.option("--repo", help = "your repository to push docker image")
@click.option("--version", help = "the version of docker image to be released")
@click.option("--registry", help = "your registry to push docker image")
@click.pass_context
def publisher_group(ctx, repo, version, registry=None):
    ctx.obj = {'repo' : repo, 
               'version' : version,
               'registry' : registry}

@click.command()
@click.pass_context
def download(ctx):
    obj = Publisher(repo=ctx.obj['repo'], version=ctx.obj['version'], registry=ctx.obj['registry'])
    obj.download()

@click.command()
@click.pass_context
def check(ctx):
    obj = Publisher(repo=ctx.obj['repo'], version=ctx.obj['version'], registry=ctx.obj['registry'])
    obj.check()

@click.command()
@click.pass_context
def push(ctx):
    obj = Publisher(repo=ctx.obj['repo'], version=ctx.obj['version'], registry=ctx.obj['registry'])
    obj.build_and_push()

@click.command()
@click.pass_context
def publish(ctx):
    obj = Publisher(repo=ctx.obj['repo'], version=ctx.obj['version'], registry=ctx.obj['registry'])
    obj.publish()

publisher_group.add_command(download)
publisher_group.add_command(push)
publisher_group.add_command(check)
publisher_group.add_command(publish)

if __name__ == '__main__':
    publisher_group()
