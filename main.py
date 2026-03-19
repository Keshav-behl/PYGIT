from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional, Tuple
import zlib
from dataclasses import dataclass
import difflib

class GitObject:
    def __init__(self,obj_type:str,content:bytes):
        self.type=obj_type
        self.content=content

    def hash(self)->str:
        header=f"{self.type} {len(self.content)}\0".encode()
        store=header+self.content
        sha1=hashlib.sha1(store).hexdigest()
        return sha1

    def serialize(self)->bytes:
        header=f"{self.type} {len(self.content)}\0".encode()
        store=header+self.content
        return zlib.compress(store)

    @classmethod
    def deserialize(cls,data:bytes)->"GitObject":
        decompressed=zlib.decompress(data)
        null_idx=decompressed.find(b"\0")
        header=decompressed[:null_idx]
        content=decompressed[null_idx+1:]

        obj_type,size=header.split(b" ")

        return cls(obj_type.decode(),content)

class Blob(GitObject):
    def __init__(self,content:bytes):
        super().__init__("blob",content)

@dataclass
class TreeEntry:
    mode:str
    name:str
    sha1:str

class Tree(GitObject):
    def __init__(self,entries:List[TreeEntry]):
        content=b""
        for entry in sorted(entries,key=lambda e:e.name):
            header=f"{entry.mode} {entry.name}\0".encode()
            binary_sha1=bytes.fromhex(entry.sha1)
            content+=header+binary_sha1
        super().__init__("tree",content)

class Commit(GitObject):
    def __init__(self,tree_sha1:str,parent_sha1:Optional[str],message:str):
        author="Pygit user ayushkashyap"
        timestamp=int(time.time())

        lines=[f"tree {tree_sha1}"]
        if parent_sha1:
            lines.append(f"parent {parent_sha1}")

        lines.append(f"author {author} {timestamp}")
        #lines.append(f"committer {author} {timestamp} +0000")
        lines.append("")
        lines.append(message)

        content="\n".join(lines).encode()

        super().__init__("commit",content)

class Repository:
    def __init__(self, path="."):
        self.path = Path(path).resolve()
        self.git_dir = self.path / ".pygit"

        #.git/objects
        self.objects_dir = self.git_dir / "objects"

        #.git/refs
        self.ref_dir = self.git_dir / "refs"
        self.heads_dir = self.ref_dir / "heads"

        #HEAD file
        self.head_file = self.git_dir / "HEAD"

        #.git/index
        self.index_file = self.git_dir / "index"

    def init(self) -> bool:
        if self.git_dir.exists():
            return False

        #create directories
        self.git_dir.mkdir()
        self.objects_dir.mkdir()
        self.ref_dir.mkdir()
        self.heads_dir.mkdir()
        #self.index_file.mkdir()

        #create initial HEAD pointing to a branch
        self.head_file.write_text("ref: refs/heads/master\n")
        self.save_index({})

        print(f"Initialized empty Git repository in {self.git_dir}")

        return True

    def load_index(self)->Dict[str,str]:
        if not self.index_file.exists():
            return {}
        try:
            with open(self.index_file,'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
                return {}

    def save_index(self,index:Dict[str,str]):
        self.index_file.write_text(json.dumps(index,indent=2))

    def add_file(self,path:str):
        if not path.exists():
            raise FileNotFoundError(f"File {path} does not exist")
        content=path.read_bytes()

        blob=Blob(content)
        sha1_hash=blob.hash()
        object_dir=self.objects_dir/sha1_hash[:2]
        object_file=object_dir/sha1_hash[2:]

        if not object_dir.exists():
            object_dir.mkdir()

        if not object_file.exists():
            with open(object_file,'wb') as f:
                f.write(blob.serialize())

        return sha1_hash

    def write_tree(self)->str:
        index=self.load_index()
        if not index:
            return ""
        
        tree_structure={}
        for path,sha1 in index.items():
            parts=path.split("/")
            current_level=tree_structure
            for i,part in enumerate(parts):
                if i==len(parts)-1:
                    current_level[part]=sha1
                else:
                    if part not in current_level:
                        current_level[part]={}
                    current_level=current_level[part]

        return self._build_tree_recursive(tree_structure)

    def _build_tree_recursive(self,tree_dict:Dict[str,any])->str:
        entries=[]
        for name,value in tree_dict.items():
            if isinstance(value,str):
                entries.append(TreeEntry(mode="100644",name=name,sha1=value))
            elif isinstance(value,dict):
                subtree_sha1=self._build_tree_recursive(value)
                entries.append(TreeEntry(mode="040000",name=name,sha1=subtree_sha1))
        
        tree=Tree(entries)
        sha1_hash=tree.hash()

        object_dir=self.objects_dir/sha1_hash[:2]
        object_file=object_dir/sha1_hash[2:]

        if not object_file.exists():
            object_dir.mkdir(exist_ok=True)
        if not object_file.exists():
            with open(object_file,'wb') as f:
                f.write(tree.serialize())

        return sha1_hash

    def get_head_sha(self) -> Optional[str]:
        if not self.head_file.exists():
            return None
        
        head_content = self.head_file.read_text().strip()
        if head_content.startswith("ref:"):
            ref_path = self.git_dir / head_content.split(" ")[1]
            if ref_path.exists():
                return ref_path.read_text().strip()
        return None

    def _get_tree_contents(self,tree_sha1:str,path_prefix:str="")->Dict[str, str]:
        contents={}
        tree_obj=self.read_object(tree_sha1)
        if tree_obj.type!="tree":
            raise TypeError(f"Object {tree_sha1} is not a tree!")
        current_pos=0
        content_bytes=tree_obj.content
        while current_pos<len(content_bytes):
            space_pos=content_bytes.find(b' ', current_pos)
            mode=content_bytes[current_pos:space_pos].decode()
            
            null_pos=content_bytes.find(b'\0', space_pos)
            name=content_bytes[space_pos + 1:null_pos].decode()
            
            start_hash = null_pos + 1
            end_hash = start_hash + 20
            binary_sha1 = content_bytes[start_hash:end_hash]
            hex_sha1 = binary_sha1.hex()
            full_path = f"{path_prefix}{name}"
            if mode == "100644":
                contents[full_path] = hex_sha1
            elif mode == "040000":
                sub_tree_contents = self._get_tree_contents(
                    tree_sha1=hex_sha1,
                    path_prefix=f"{full_path}/"
                )
                contents.update(sub_tree_contents)
            current_pos = end_hash
            
        return contents

    def commit(self,message:str)->str:
        tree_sha1=self.write_tree()
        if not tree_sha1:
            raise Exception("Cannot commit with an empty staging area.")
        
        parent_sha1=self.get_head_sha()
        commit=Commit(tree_sha1,parent_sha1,message)
        commit_sha1=commit.hash()
        #print("hit 1")
        object_dir=self.objects_dir/commit_sha1[:2]
        object_file=object_dir/commit_sha1[2:]
        if not object_dir.exists():
            object_dir.mkdir()
        #print("hit 2")
        with open(object_file,'wb') as f:
            f.write(commit.serialize())

        head_content=self.head_file.read_text().strip()
        ref_path_str=head_content.split(" ")[1]
        ref_path=self.git_dir/ref_path_str

        ref_path.parent.mkdir(parents=True,exist_ok=True)
        ref_path.write_text(f"{commit_sha1}\n")

        print(f"Committed to master: {commit_sha1[:7]} {message.splitlines()[0]}")
        return commit_sha1


    def read_object(self,sha1:str)->GitObject:
        object_path=self.objects_dir/sha1[:2]/sha1[2:]
        #print(object_path)
        if not object_path.exists():
            raise FileNotFoundError(f"Object {sha1} not found in databsse")
        
        with open(object_path,'rb') as f:
            compressed_data=f.read()
        
        return GitObject.deserialize(compressed_data)

    def log(self):
        current_commit_sha=self.get_head_sha()

        if not current_commit_sha:
            print("No commits yet")
            return

        while current_commit_sha:
            commit=self.read_object(current_commit_sha)

            content_str=commit.content.decode()
            lines=content_str.splitlines()

            print(f"commit {current_commit_sha}")
            author_line=[l for l in lines if l.startswith("author")][0]
            print(f"Author: {author_line.split(' ', 1)[1]}")

            message_start_idx=lines.index("")+1
            message="\n".join(lines[message_start_idx:])
            print(f"  {message.strip()}")
            print("")

            parent_sha=None
            for line in lines:
                if line.startswith("parent"):
                    parent_sha=line.split(" ")[1]
                    break

            current_commit_sha=parent_sha

    def _print_tree(self,content:bytes):
        current_pos=0
        while current_pos<len(content):
            space_pos=content.find(b' ',current_pos)
            mode=content[current_pos:space_pos].decode()

            null_pos=content.find(b'\0',space_pos)
            name=content[space_pos+1:null_pos].decode()

            start_hash = null_pos + 1
            end_hash = start_hash + 20
            binary_sha1 = content[start_hash:end_hash]
            hex_sha1 = binary_sha1.hex()
            obj_type = "blob" if mode == "100644" else "tree"
            print(f"{mode} {obj_type} {hex_sha1}\t{name}")
            current_pos = end_hash

    def checkout(self,sha1:str):
        obj=self.read_object(sha1)
        checkout_files={}
        if obj.type=="commit":
            content_str=obj.content.decode()
            tree_line=[line for line in content_str.splitlines() if line.startswith("tree")][0]
            tree_sha1=tree_line.split(" ")[1]
            checkout_files=self._get_tree_contents(tree_sha1)        
        else:
            raise TypeError(f"this is not a commit hash {sha1}")
        index=self.load_index()

        missing_content={}
        changed_content={}
        extra_content={}

        for path,hash_ch in checkout_files.items():
            if path not in index:
                missing_content[path]=hash_ch
            elif hash_ch!=index[path]:
                changed_content[path]=hash_ch

        for path,hash_idx in index.items():
            if path not in checkout_files:
                extra_content[path]=hash_idx

        if missing_content:
            print("Files which need to be added")
            for path,hash in missing_content.items():
                print(f"\t{self.path/path}")
                miss_obj=self.read_object(hash)
                #print(miss_obj.content.decode())
                miss_dir=self.path/path
                miss_dir.parent.mkdir(parents=True,exist_ok=True)
                blob_obj=self.read_object(hash)
                miss_dir.write_bytes(blob_obj.content)


        if extra_content:
            print("Files to be delete")
            for path,hash in extra_content.items():
                print(f"\t{self.path/path}")
                del_dir=self.path/path
                if del_dir.exists() and del_dir.is_file():
                    del_dir.unlink()

        if changed_content:
            print("Files to  be changed")
            for path,hash in changed_content.items():
                print(f"\t{self.path/path}")
                cha_dir=self.path/path
                blob_obj=self.read_object(hash)
                cha_dir.write_bytes(blob_obj.content)
            
        dir_commit=self.heads_dir/"master"
        dir_commit.write_text(sha1)
        self.save_index(checkout_files)

    def diff(self):
        index=self.load_index()
        full_path=self.path
        if not full_path.exists():
            raise FileNotFoundError(f"Path {path} does not exist")
        working_dir={}
        for item in full_path.rglob("*"):
            try:
                relative_path_str=str(item.relative_to(full_path))
            except:
                continue
            
            if ".pygit" in item.parts:
                continue
            if ".git" in item.parts:
                continue

            if item.is_file():
                content=item.read_bytes()
                blob=Blob(content)
                sha1_hash=blob.hash()
                working_dir[relative_path_str]=sha1_hash

        untracked_files=[]
        diff_files=[]
        for path,hash in working_dir.items():
            if path not in index:
                untracked_files.append(path)
            elif hash!=index[path]:
                diff_files.append(path)


        for path in diff_files:
            blob_hash=index[path]
            blob_obj=self.read_object(blob_hash)
            content_idx=blob_obj.content.decode().splitlines()
            full_path=self.path/path
            content_wd=full_path.read_text().splitlines()
            

            diff_generator=difflib.unified_diff(content_idx,content_wd,fromfile="index",tofile="working directory",lineterm="")
            print(path)
            try:
                print(next(diff_generator))
                print(next(diff_generator))
            except StopIteration:
                continue

            for line in diff_generator:
                print(line)
            
            print("")



    def branch(self,branch_name:str):
        if branch_name is None:
            full_path=self.heads_dir
            current_branch=""
            head_content=self.head_file.read_text().strip()
            if head_content.startswith("ref: refs/heads/"):
                current_branch=head_content.split("/")[-1]

            for branch_path in self.heads_dir.iterdir():
                if branch_path.is_file():
                    branch=branch_path.name
                    if branch==current_branch:
                        print(f"-> {branch}")
                    else:
                        print(f"   {branch}")
        else:
            new_branch=self.heads_dir/branch_name
            if new_branch.exists():
                print(f"Branch names {branch_name} already exists")
                return
            else:
                current_commit_sha=self.get_head_sha()
                if not current_commit_sha:
                    print("No commits to add new branch")
                    return 
                else:
                    new_branch.write_text(current_commit_sha+"/n")
                    print(f"Branch named {branch_name} has been created at last commit")



    def cat_file(self,sha1:str,pretty_print:bool=True):
        obj=self.read_object(sha1)
        #print(obj.type[1:])
        if obj.type=="blob":
            print(obj.content.decode())
        elif obj.type=="commit":
            print(obj.content.decode())
        elif obj.type=="tree":
            self._print_tree(obj.content)
        else:
            raise TypeError(f"Unknown object type")

    def status(self):
        index=self.load_index()
        full_path=self.path
        if not full_path.exists():
            raise FileNotFoundError(f"Path {path} does not exist")

        head_sha1=self.get_head_sha()
        working_dir={}
        head_files={}
        if head_sha1:
            commit_obj=self.read_object(head_sha1)
            content_str=commit_obj.content.decode()
            tree_line=[line for line in content_str.splitlines() if line.startswith("tree")][0]
            tree_sha1=tree_line.split(" ")[1]
            head_files=self._get_tree_contents(tree_sha1)
            

        for item in full_path.rglob("*"):
            try:
                relative_path_str=str(item.relative_to(full_path))
            except ValueError:
                continue

            if ".pygit" in item.parts:
                continue

            if ".git" in item.parts:
                continue

            if item.is_file():
                content=item.read_bytes()
                blob=Blob(content)
                sha1_hash=blob.hash()

                working_dir[relative_path_str]=sha1_hash

        staged_changes={}
        unstaged_changes={}
        untracked_files=[]

        #staged_changes=self._get_tree_contents()
        uncommitted_changes={}


        for path,hash_head in head_files.items():
            if path not in index:
                unstaged_changes[path]="deleted"
            elif hash_head!=index[path]:
                uncommitted_changes[path]="modified"

        for path,hash_wd in working_dir.items():
            if path not in index:
                untracked_files.append(path)
            elif hash_wd!=index[path]:
                unstaged_changes[path]="modified"

        for path,hash_index in index.items():
            if path not in working_dir:
                unstaged_changes[path]="deleted"

        if uncommitted_changes:
            print("Changes left to be committed")
            for path,status in uncommitted_changes.items():
                if status=="modified":
                    print(f"\t{path}")

        if unstaged_changes:
            print("Chnages not committed")
            for path,status in unstaged_changes.items():
                print(f"\t{status}:{path}")

        if untracked_files:
            print("\nUtracked files")
            for path in untracked_files:
                print(f"\t{path}")

        return 


    def add_path(self,path:str)->None:
        full_path=self.path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Path {path} does not exist")

        index=self.load_index()

        if full_path.is_file():
            sha1_hash=self.add_file(full_path)
            relative_path=str(full_path.relative_to(self.path))
            index[relative_path]=sha1_hash
            print(f"Added file {relative_path} with hash {sha1_hash}")
        elif full_path.is_dir():
            for item in full_path.rglob("*"):
                if item.is_file():
                    relative_path=str(item.relative_to(self.path))
                    sha1_hash=self.add_file(item)
                    index[relative_path]=sha1_hash
                    print(f"Added file {relative_path} with hash {sha1_hash}")
        else:
            raise ValueError(f"Path {path} is neither a file nor a directory")  

        self.save_index(index)


def main():
    parser = argparse.ArgumentParser(description="PyGit - A simple git clone!")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    #init command
    init_parser = subparsers.add_parser("init", help="Initialize a new repository")
    #add command
    add_parser=subparsers.add_parser("add",help="Add file contents to the staging area")
    add_parser.add_argument("paths",nargs="+",help="Files and directories to add")

    write_tree_parser=subparsers.add_parser("write-tree",help="Create a tree object from the current index")
    commit_parser=subparsers.add_parser("commit",help="Record chnages to the repository")
    commit_parser.add_argument("-m","--message",required=True,help="Commit message")
    log_parser=subparsers.add_parser("log",help="Show commit history")

    cat_file_parser=subparsers.add_parser("cat-file",help="Provide content for repository objects")
    cat_file_parser.add_argument("sha1",help="The SHA1 hash of the object to inspect")

    status_parser=subparsers.add_parser("status",help="Give us inforomation about the files in three directories")
    checkout_parser=subparsers.add_parser("checkout",help="Helps us travel back to a particular commit")
    checkout_parser.add_argument("sha1",help="The SHA1 hash of the commit u want to mave back to.")

    branch_parser=subparsers.add_parser("branch",help="Helps us to list all the available branches")
    branch_parser.add_argument("branch_name",nargs="?",default=None,help="Helps us create a new branch")

    diff_parser=subparsers.add_parser("diff",help="Helps the user spot the diffrences in code in Index and woriking directory")
    #diff_parser.add_argument()


    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    repo = Repository()
    try:
        if args.command == "init":
            if not repo.init():
                print("Repository already exists")
                return
        elif args.command=="add":
            if not repo.git_dir.exists():
                print("Not a git repository")
                return
            for path in args.paths:
                repo.add_path(path)
                print(f"Adding {path} to the staging area")
        elif args.command=="write-tree":
            if not repo.git_dir.exists():
                print("Not a git repository")
                return 

            tree_sha1=repo.write_tree()
            print(f"Root tree SHA1: {tree_sha1}")
        elif args.command=="commit":
            if not repo.git_dir.exists():
                print("Not a git repository")
                return
            repo.commit(args.message)
        elif args.command=="log":
            if not repo.git_dir.exists():
                print("Not a git repo")
                return
            repo.log()
        elif args.command=="cat-file":
            if not repo.git_dir.exists():
                print("No repo exixts")
                return
            repo.cat_file(args.sha1)
        elif args.command=="status":
            if not repo.git_dir.exists():
                print("No repo exists")
                return
            repo.status()
        elif args.command=="checkout":
            if not repo.git_dir.exists():
                print("No repo exists")
                return
            repo.checkout(args.sha1)
        elif args.command=="branch":
            if not repo.git_dir.exists():
                print("No repo exixts")
                return
            repo.branch(args.branch_name)
        elif args.command=="diff":
            if not repo.git_dir.exists():
                print("No repo")
                return
            repo.diff()

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


main()
